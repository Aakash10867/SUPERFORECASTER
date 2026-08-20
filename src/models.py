"""
Model access with fallback chains.

The free tier gives us a lot of models with small individual quotas rather than
one model with a big quota. So every task is defined as an ORDERED LIST of
models: try the first, and on rate-limit / unavailability / error, fall through
to the next. A task only fails if every model in its chain fails.

This also means a wrong model name in models.yaml is survivable -- the call
just falls through to the next one, and the failure is recorded in the run log
so you can correct it.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

import requests

from . import config

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"


@dataclass
class CallStats:
    """Tracks usage so we respect per-model limits within a single run."""
    calls_by_model: dict = field(default_factory=lambda: defaultdict(int))
    failures_by_model: dict = field(default_factory=lambda: defaultdict(list))
    total_calls: int = 0
    exhausted: set = field(default_factory=set)

    def summary(self) -> str:
        lines = []
        for model, n in sorted(self.calls_by_model.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {model}: {n} calls")
        if self.failures_by_model:
            lines.append("  failures:")
            for model, errs in self.failures_by_model.items():
                # only show the distinct error kinds, not every occurrence
                kinds = sorted(set(errs))
                lines.append(f"    {model}: {', '.join(kinds[:4])}")
        return "\n".join(lines) if lines else "  (no model calls)"


class ModelRouter:
    def __init__(self, settings: dict, models_cfg: dict, log):
        self.settings = settings
        self.limits = models_cfg.get("limits", {}) or {}
        self.chains = models_cfg.get("chains", {}) or {}
        self.log = log
        self.stats = CallStats()
        self.key = config.api_key()
        self._last_call_at: dict[str, float] = {}
        self._max_calls = settings["run"]["max_calls_per_run"]
        self._backoff = settings["run"]["retry_backoff_seconds"]
        self._max_retries = settings["run"]["max_retries_per_model"]

    # -- limit bookkeeping ---------------------------------------------------

    def _rpd(self, model: str) -> int:
        return int(self.limits.get(model, {}).get("rpd", 20))

    def _rpm(self, model: str) -> int:
        return int(self.limits.get(model, {}).get("rpm", 5))

    def _available(self, model: str) -> bool:
        if model in self.stats.exhausted:
            return False
        return self.stats.calls_by_model[model] < self._rpd(model)

    def _throttle(self, model: str) -> None:
        """Space calls out so we stay under requests-per-minute."""
        rpm = self._rpm(model)
        if rpm <= 0:
            return
        min_gap = 60.0 / rpm
        last = self._last_call_at.get(model)
        if last is not None:
            wait = min_gap - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_call_at[model] = time.time()

    # -- the main entry point ------------------------------------------------

    def generate(
        self,
        task: str,
        prompt: str,
        *,
        expect_json: bool = True,
        temperature: float = 0.4,
        max_output_tokens: int = 4096,
    ):
        """
        Run `prompt` through the fallback chain for `task`.

        Returns (parsed_result, model_name) on success, or (None, None) if every
        model in the chain failed. Callers must handle None -- a failed call
        should never crash the run.
        """
        chain = self.chains.get(task) or []
        if not chain:
            self.log.warn(f"No model chain configured for task '{task}'")
            return None, None

        if self.stats.total_calls >= self._max_calls:
            self.log.warn(
                f"Run call ceiling ({self._max_calls}) reached; skipping task '{task}'"
            )
            return None, None

        for model in chain:
            if not self._available(model):
                continue

            for attempt in range(self._max_retries + 1):
                self._throttle(model)
                # A truncated response means the budget was too small, not that
                # the model is bad -- so retry the same model with more room.
                budget = max_output_tokens * (2 ** attempt)
                ok, payload, err = self._post(model, prompt, temperature,
                                              budget, expect_json)
                self.stats.calls_by_model[model] += 1
                self.stats.total_calls += 1

                if ok:
                    parsed = _parse_json(payload) if expect_json else payload
                    if parsed is None and expect_json:
                        self.stats.failures_by_model[model].append("unparseable-json")
                        # A malformed response is worth one retry, then move on.
                        if attempt < self._max_retries:
                            continue
                        break
                    return parsed, model

                self.stats.failures_by_model[model].append(err)

                if err == "truncated" and attempt < self._max_retries:
                    continue

                if err in ("rate-limited", "quota-exhausted"):
                    # No point retrying this model within the same run.
                    self.stats.exhausted.add(model)
                    break
                if err in ("not-found", "bad-request"):
                    # Almost certainly a wrong model name. Skip it permanently
                    # and make the reason loud in the log.
                    self.stats.exhausted.add(model)
                    self.log.warn(
                        f"Model '{model}' rejected the request ({err}). "
                        "Check the name in config/models.yaml -- run verify_models.py "
                        "to see the models your key can actually use."
                    )
                    break
                if attempt < self._max_retries:
                    time.sleep(self._backoff)

        self.log.warn(f"All models failed for task '{task}'")
        return None, None

    def _post(self, model: str, prompt: str, temperature: float, max_tokens: int,
              expect_json: bool = True):
        url = f"{API_ROOT}/{model}:generateContent"
        gen_config = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if expect_json:
            # Ask the API to guarantee syntactically valid JSON rather than
            # hoping the model obeys the instruction. Without this, the deep
            # models in particular wrap output in prose and code fences, and
            # a run can lose several calls to unparseable responses.
            gen_config["responseMimeType"] = "application/json"

        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        }
        try:
            r = requests.post(
                url,
                headers={
                    "x-goog-api-key": self.key,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=180,
            )
        except requests.RequestException as exc:
            return False, None, f"network:{type(exc).__name__}"

        if r.status_code == 200:
            try:
                data = r.json()
                cand = data["candidates"][0]
                parts = cand.get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                if not text.strip():
                    return False, None, "empty-response"
                if cand.get("finishReason") == "MAX_TOKENS":
                    # Truncated output is unparseable JSON. Report it distinctly
                    # so the log tells us to raise the token budget rather than
                    # sending us hunting for a prompt problem.
                    return False, None, "truncated"
                return True, text, None
            except (KeyError, IndexError, ValueError):
                return False, None, "malformed-response"

        if r.status_code == 429:
            return False, None, "rate-limited"
        if r.status_code == 404:
            return False, None, "not-found"
        if r.status_code == 400:
            return False, None, "bad-request"
        if r.status_code == 403:
            return False, None, "forbidden"
        if 500 <= r.status_code < 600:
            return False, None, f"server-{r.status_code}"
        return False, None, f"http-{r.status_code}"

    # -- embeddings ----------------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """
        Embed a list of strings, using the BATCH endpoint.

        An earlier version sent one HTTP request per string. Combined with a
        caller that re-embedded the whole set for every comparison, a day with
        172 articles would have needed roughly thirty thousand requests -- it
        rate-limited within seconds and silently degraded to lexical matching.
        Batching plus the cache in Similarity turns that into a handful of
        calls.

        Returns None if embeddings are unavailable, in which case callers fall
        back to local lexical similarity.
        """
        if not texts:
            return []

        chain = self.chains.get("embed") or []
        batch_size = 64

        for model in chain:
            if not self._available(model):
                continue

            out: list[list[float]] = []
            failed = False

            for start in range(0, len(texts), batch_size):
                batch = texts[start:start + batch_size]
                self._throttle(model)
                url = f"{API_ROOT}/{model}:batchEmbedContents"
                body = {
                    "requests": [
                        {
                            "model": f"models/{model}",
                            "content": {"parts": [{"text": t[:8000]}]},
                        }
                        for t in batch
                    ]
                }
                try:
                    r = requests.post(
                        url,
                        headers={"x-goog-api-key": self.key,
                                 "Content-Type": "application/json"},
                        json=body,
                        timeout=180,
                    )
                except requests.RequestException:
                    failed = True
                    break

                self.stats.calls_by_model[model] += 1
                self.stats.total_calls += 1

                if r.status_code != 200:
                    self.stats.failures_by_model[model].append(
                        f"embed-http-{r.status_code}")
                    if r.status_code in (400, 403, 404):
                        self.stats.exhausted.add(model)
                    if r.status_code == 429:
                        # Back off once, then give up on this model.
                        time.sleep(self._backoff)
                        self.stats.exhausted.add(model)
                    failed = True
                    break

                try:
                    vectors = [e["values"] for e in r.json()["embeddings"]]
                except (KeyError, ValueError, TypeError):
                    self.stats.failures_by_model[model].append("embed-malformed")
                    failed = True
                    break

                if len(vectors) != len(batch):
                    failed = True
                    break
                out.extend(vectors)

            if not failed and len(out) == len(texts):
                return out

        return None


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _parse_json(text: str):
    """
    Models wrap JSON in prose and code fences no matter how firmly you ask them
    not to. Try progressively more forgiving strategies.
    """
    if text is None:
        return None
    candidates = []

    m = _FENCE.search(text)
    if m:
        candidates.append(m.group(1))
    candidates.append(text)

    # Grab the outermost {...} or [...] block
    for opener, closer in (("[", "]"), ("{", "}")):
        i = text.find(opener)
        j = text.rfind(closer)
        if i != -1 and j > i:
            candidates.append(text[i:j + 1])

    for cand in candidates:
        cand = cand.strip()
        if not cand:
            continue
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            # Trailing commas are the single most common model mistake.
            repaired = re.sub(r",(\s*[}\]])", r"\1", cand)
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                continue
    return None
