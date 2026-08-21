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

from . import config, quota

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

# Embedding pacing. The per-minute quota counts individual embeddings rather
# than HTTP requests, and pacing exactly at the stated rate leaves no room for
# jitter -- see embed().
EMBED_RATE_HEADROOM = 0.7
EMBED_BACKOFF_SECONDS = 30
EMBED_MAX_RETRIES = 3


@dataclass
class CallStats:
    """Tracks usage so we respect per-model limits within a single run."""
    calls_by_model: dict = field(default_factory=lambda: defaultdict(int))
    failures_by_model: dict = field(default_factory=lambda: defaultdict(list))
    total_calls: int = 0
    # (key_name, model) pairs that are done for this run.
    #
    # THE BUG THIS FIXES
    # ------------------
    # This used to hold bare model names. A 429 on ONE key -- which on the free
    # tier usually means "you are going too fast this minute", not "you are out
    # for the day" -- therefore disabled that model on BOTH keys for the rest
    # of the run. On live run 3 that killed embeddings after 11 calls on key
    # one while key two still had a thousand unused, so article deduplication
    # collapsed (237 articles -> 235 "distinct" stories) and the lexicon
    # matcher started inventing tags it had correctly merged the run before.
    exhausted: set = field(default_factory=set)
    last_error: str = ""   # error from the most recent failed call

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
        if self.exhausted:
            lines.append("  retired during this run (key, model):")
            for key_name, model in sorted(self.exhausted):
                lines.append(f"    {model} on {key_name}")
        return "\n".join(lines) if lines else "  (no model calls)"


class ModelRouter:
    def __init__(self, settings: dict, models_cfg: dict, log):
        self.settings = settings
        self.limits = models_cfg.get("limits", {}) or {}
        self.chains = models_cfg.get("chains", {}) or {}
        self.log = log
        self.stats = CallStats()

        # Two keys from two different projects = two separate quota pools.
        self.keys = config.api_keys()                 # [(env_name, key), ...]
        self.key_names = [n for n, _ in self.keys]
        self.key_by_name = {n: k for n, k in self.keys}
        self.key = self.keys[0][1]                    # legacy single-key attr

        # Persistent daily counts. See src/quota.py for why this exists.
        self.quota = quota.Quota(self.key_names)

        # Deep models are scarce (20/day each). Generation's `contest` step is
        # the most important judgement in the whole system, so it gets a hard
        # reserve that forecasting may not touch. A bad question is unfixable;
        # a slightly worse forecast is refreshed within a week.
        self.deep_models = set(models_cfg.get("deep_models", []) or [])
        self.forecast_tasks = set(models_cfg.get("forecast_tasks", []) or [])
        self.grounding_models = set(models_cfg.get("grounding_models", []) or [])
        self._deep_reserve = int(
            settings.get("run", {}).get("deep_reserve_for_generation", 0)
        )

        # Populated by the most recent grounded call; see generate(grounded=True).
        self.last_grounding: dict = {}

        self._last_call_at: dict[str, float] = {}
        self._max_calls = settings["run"]["max_calls_per_run"]
        self._backoff = settings["run"]["retry_backoff_seconds"]
        self._max_retries = settings["run"]["max_retries_per_model"]

    # -- limit bookkeeping ---------------------------------------------------

    def _rpd(self, model: str) -> int:
        return int(self.limits.get(model, {}).get("rpd", 20))

    def _rpm(self, model: str) -> int:
        return int(self.limits.get(model, {}).get("rpm", 5))

    def _pick_key(self, model: str) -> str | None:
        """
        Return the name of the key with the most remaining quota for `model`,
        or None if every key is exhausted for it.

        Picking the emptiest key rather than always starting at key one keeps
        both pools draining evenly, so a burst never strands capacity.
        """
        best, best_left = None, 0
        for name in self.key_names:
            if (name, model) in self.stats.exhausted:
                continue
            left = self.quota.remaining(name, model, self._rpd(model))
            if left > best_left:
                best, best_left = name, left
        return best

    def _deep_remaining(self) -> int:
        """Total deep-model calls left today, across every key."""
        total = 0
        for model in self.deep_models:
            rpd = self._rpd(model)
            for name in self.key_names:
                if (name, model) in self.stats.exhausted:
                    continue
                total += self.quota.remaining(name, model, rpd)
        return total

    def _key_available(self, key_name: str, model: str) -> bool:
        if (key_name, model) in self.stats.exhausted:
            return False
        return self.quota.remaining(key_name, model, self._rpd(model)) > 0

    def _available(self, model: str, task: str = "") -> bool:
        if self._pick_key(model) is None:
            return False
        # Forecasting may not eat into generation's deep reserve.
        if (
            task in self.forecast_tasks
            and model in self.deep_models
            and self._deep_remaining() <= self._deep_reserve
        ):
            return False
        return True

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
        grounded: bool = False,
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
            if not self._available(model, task):
                continue
            if grounded and model not in self.grounding_models:
                # Only some models can search. Silently skipping a model that
                # cannot ground would give us an ungrounded answer that LOOKS
                # verified, which is the one failure we cannot tolerate here.
                continue

            for attempt in range(self._max_retries + 1):
                key_name = self._pick_key(model)
                if key_name is None:
                    break
                self._throttle(model)
                # A truncated response means the budget was too small, not that
                # the model is bad -- so retry the same model with more room.
                budget = max_output_tokens * (2 ** attempt)
                ok, payload, err = self._post(model, prompt, temperature,
                                              budget, expect_json,
                                              key_name=key_name,
                                              grounded=grounded)
                self.stats.calls_by_model[model] += 1
                self.stats.total_calls += 1
                # Record and flush immediately. If the run crashes later, the
                # calls it already made must still count against today.
                self.quota.record(key_name, model)
                self.quota.flush()

                if ok:
                    if grounded:
                        payload, self.last_grounding = payload
                    parsed = _parse_json(payload) if expect_json else payload
                    if parsed is None and expect_json:
                        self.stats.failures_by_model[model].append("unparseable-json")
                        self.stats.last_error = f"{model}: unparseable-json"
                        # A malformed response is worth one retry, then move on.
                        if attempt < self._max_retries:
                            continue
                        break
                    return parsed, model

                self.stats.failures_by_model[model].append(err)
                self.stats.last_error = f"{model}: {err}"

                if err == "truncated" and attempt < self._max_retries:
                    continue

                if err in ("rate-limited", "quota-exhausted"):
                    # "Too fast this minute" and "out for the day" arrive as
                    # the same 429 but need OPPOSITE responses. Retire only
                    # this key for this model, then try the other key -- which
                    # has its own separate per-minute and per-day allowance.
                    self.stats.exhausted.add((key_name, model))
                    other = self._pick_key(model)
                    if other is not None:
                        self.log.info(
                            f"    {model} rate-limited on {key_name}; "
                            f"switching to {other}"
                        )
                        continue
                    break
                if err in ("not-found", "bad-request"):
                    # A wrong model name is wrong on every key, so this one IS
                    # global.
                    for kn in self.key_names:
                        self.stats.exhausted.add((kn, model))
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
              expect_json: bool = True, key_name: str | None = None,
              grounded: bool = False):
        url = f"{API_ROOT}/{model}:generateContent"
        key = self.key_by_name.get(key_name or "", self.key)
        gen_config = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if expect_json and not grounded:
            # Search grounding and forced-JSON output cannot be combined on the
            # Gemini API. When grounding, we ask for JSON in the prompt instead
            # and lean on _parse_json to dig it out of the prose.
            #
            # Otherwise: ask the API to guarantee syntactically valid JSON
            # rather than hoping the model obeys the instruction. Without this,
            # the deep models in particular wrap output in prose and code
            # fences, and a run can lose several calls to unparseable responses.
            gen_config["responseMimeType"] = "application/json"

        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        }
        if grounded:
            body["tools"] = [{"google_search": {}}]
        try:
            r = requests.post(
                url,
                headers={
                    "x-goog-api-key": key,
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
                if grounded:
                    # The API happily answers an ungrounded question even when
                    # you ask for search -- you get a normal-looking reply with
                    # no groundingMetadata. That would give us a reference-class
                    # entry marked "verified" that was never verified, so we
                    # report what actually happened rather than assuming.
                    meta = cand.get("groundingMetadata") or {}
                    sources = []
                    for chunk in (meta.get("groundingChunks") or []):
                        web = chunk.get("web") or {}
                        title = web.get("title") or web.get("uri") or ""
                        if title:
                            sources.append(title)
                    queries = meta.get("webSearchQueries") or []
                    info = {
                        "fired": bool(meta),
                        "sources": sources[:10],
                        "queries": queries[:5],
                    }
                    return True, (text, info), None
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
        # The per-minute quota counts individual EMBEDDINGS, not HTTP requests.
        # A batch of 64 therefore consumes 64 of a 100/minute allowance, so two
        # batches in quick succession trip the limit -- which is exactly what
        # happened on the first two live runs. Keep batches small and pace them
        # by the number of texts they contain.
        batch_size = 20

        for model in chain:
            if not self._available(model):
                continue

            # Pace with headroom. The per-minute embedding quota counts
            # individual EMBEDDINGS, not requests, so 100/min with batches of
            # 20 means one batch every 12 seconds -- exactly at the ceiling,
            # with no slack for network jitter or clock granularity. Live run 2
            # got away with it; live run 3 did not.
            rpm = max(int(self._rpm(model) * EMBED_RATE_HEADROOM), 1)
            out: list[list[float]] = []
            failed = False

            start = 0
            retries_this_batch = 0
            while start < len(texts):
                start_over = False
                batch = texts[start:start + batch_size]

                # Choose the key PER BATCH, not once per call. Previously one
                # key was chosen up front, so a 429 halfway through abandoned
                # the whole call while the other key sat unused.
                key_name = self._pick_key(model)
                if key_name is None:
                    failed = True
                    break
                embed_key = self.key_by_name.get(key_name, self.key)

                # Pace by batch size, not by call count.
                last = self._last_call_at.get(model)
                if last is not None:
                    wait = (60.0 * len(batch) / rpm) - (time.time() - last)
                    if wait > 0:
                        time.sleep(wait)
                self._last_call_at[model] = time.time()

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
                        headers={"x-goog-api-key": embed_key,
                                 "Content-Type": "application/json"},
                        json=body,
                        timeout=180,
                    )
                except requests.RequestException:
                    failed = True
                    break

                self.stats.calls_by_model[model] += 1
                self.stats.total_calls += 1
                self.quota.record(key_name, model)
                self.quota.flush()

                if r.status_code == 429:
                    # A per-minute limit genuinely clears by waiting, so this
                    # is not terminal. Retire THIS KEY for this model, hand the
                    # batch to the other key, and only sleep once both keys are
                    # rate-limited.
                    self.stats.failures_by_model[model].append("embed-429")
                    self.stats.exhausted.add((key_name, model))
                    if self._pick_key(model) is not None:
                        start_over = True          # other key, same batch
                    elif retries_this_batch < EMBED_MAX_RETRIES:
                        # Both keys limited. Wait it out and un-retire them --
                        # the limit is per MINUTE.
                        wait = EMBED_BACKOFF_SECONDS * (retries_this_batch + 1)
                        self.log.info(
                            f"    embeddings rate-limited on all keys; "
                            f"waiting {wait}s (per-minute limit, not daily)"
                        )
                        time.sleep(wait)
                        for kn in self.key_names:
                            self.stats.exhausted.discard((kn, model))
                        retries_this_batch += 1
                        start_over = True
                    else:
                        failed = True
                        break
                elif r.status_code != 200:
                    self.stats.failures_by_model[model].append(
                        f"embed-http-{r.status_code}")
                    if r.status_code in (400, 403, 404):
                        for kn in self.key_names:
                            self.stats.exhausted.add((kn, model))
                    failed = True
                    break

                if start_over:
                    continue

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
                start += batch_size
                retries_this_batch = 0

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
