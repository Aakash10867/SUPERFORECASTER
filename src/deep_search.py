"""
Full-text search via Tavily (v22).

WHY
---
Up to v21 the web check read only Google News HEADLINES. On Q0003 that was
the root of every miss: "fell short of its upper limit" read as "did not
happen", headlines carried no dates, and a single crypto headline could not
be judged. Counting two sources was a crude stand-in for judging one source
well. Tavily returns the article TEXT, so the reader can quote the sentence
that shows the act happened -- and code can check the quote is really there.

Request and response shapes were taken from Tavily's API reference
(docs.tavily.com, checked 3 Oct 2026), not from memory: POST /search with a
Bearer key; results carry title, url, content, raw_content (when asked) and
published_date (only when include_published_date is set); GET /usage reports
credits used and the limit. Error codes: 401 bad key, 429 rate limit,
432 plan limit, 433 pay-as-you-go limit, 400/422 bad parameters.

COST
----
One credit per basic search. The free plan has 1,000 a month, so this is NOT
run for every question every day. web_resolve calls it only when it matters
(a headline says "happened", a dated lead has passed) plus a weekly sweep of
each open question, and it stops spending below a reserve.

FAILURE IS LOUD AND SPECIFIC
----------------------------
DeepUnavailable carries a `kind` so the caller can tell "no key" from "out of
credits" from "rate limited" -- and alert accordingly. A parameter rejection
(400/422) is retried once with the minimum parameters, because one renamed
option must not switch full-text reading off silently.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

API = "https://api.tavily.com"
KEY_ENV = "TAVILY_API_KEY"
TIMEOUT = 45
FULL_TEXT_MIN = 400          # chars; below this the "article" is really a stub

# v23: social sites. In the 3 Oct probe they took 6 of 23 result slots and
# were nearly all stubs. Excluded in the request AND filtered again in code,
# because a request option can be silently ignored (or dropped by the
# minimal retry below).
DEFAULT_EXCLUDE = ("facebook.com", "instagram.com", "youtube.com", "x.com",
                   "twitter.com")


def excluded(site: str, domains) -> bool:
    site = (site or "").lower()
    return any(site == d or site.endswith("." + d) for d in domains)


class DeepUnavailable(RuntimeError):
    """kind: no_key | bad_key | out_of_credits | rate_limited | error"""
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


def _site(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def parse_date(value):
    """Tavily dates come as RFC 2822 or ISO 8601. Returns a date or None."""
    if not value:
        return None
    text = str(value).strip()
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        d = parsedate_to_datetime(text)
        if d.tzinfo is not None:
            d = d.astimezone(dt.timezone.utc)
        return d.date()
    except (TypeError, ValueError, IndexError):
        return None


def to_article(res: dict) -> dict:
    """One Tavily result -> the article dict the reader and judge use."""
    raw = (res.get("raw_content") or "").strip()
    snippet = (res.get("content") or "").strip()
    text = raw if len(raw) >= FULL_TEXT_MIN else ""
    d = parse_date(res.get("published_date"))
    site = _site(res.get("url", ""))
    return {
        "title": " ".join((res.get("title") or "").split()),
        "url": res.get("url", ""),
        "site": site,
        "publisher": site or "unknown",
        "date": d.isoformat() if d else "",
        "text": text,                       # full text, or "" if only a stub
        "snippet": snippet[:500],
        "full": bool(text),
    }


class TavilyClient:
    """
    Thin client. `post`/`get` are injectable for offline tests:
      post(url, json, headers) -> (status, json_or_None, text)
      get(url, headers)        -> (status, json_or_None, text)
    """

    def __init__(self, key: str | None = None, post=None, get=None,
                 max_results: int = 8, exclude=DEFAULT_EXCLUDE):
        self.key = key if key is not None else os.environ.get(KEY_ENV, "").strip()
        self._post = post or _http_post
        self._get = get or _http_get
        self.max_results = max_results
        self.exclude = tuple(d.strip().lower() for d in (exclude or ()) if d.strip())
        self.calls = 0
        self.disabled = ""            # set to a reason once a fatal error is seen

    def _headers(self):
        return {"Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json"}

    def usage(self) -> dict | None:
        """{'used': int, 'limit': int|None, 'left': int|None} or None if unknown."""
        if not self.key:
            return None
        try:
            status, data, _text = self._get(f"{API}/usage", self._headers())
        except Exception:                                  # noqa: BLE001
            return None
        if status != 200 or not isinstance(data, dict):
            return None
        k = data.get("key") or {}
        acct = data.get("account") or {}
        used = k.get("usage") if k.get("usage") is not None else acct.get("plan_usage")
        limit = k.get("limit") if k.get("limit") is not None else acct.get("plan_limit")
        try:
            used = int(used) if used is not None else None
            limit = int(limit) if limit is not None else None
        except (TypeError, ValueError):
            return None
        left = (limit - used) if (used is not None and limit is not None) else None
        return {"used": used, "limit": limit, "left": left}

    def search(self, query: str, window: tuple | None = None) -> list[dict]:
        """
        Full-text news search. Returns article dicts (see to_article).
        Raises DeepUnavailable on anything that means "cannot read today".
        """
        if self.disabled:
            raise DeepUnavailable(self.disabled, "disabled earlier this run")
        if not self.key:
            self.disabled = "no_key"
            raise DeepUnavailable("no_key", f"secret {KEY_ENV} is not set")
        body = {
            "query": query,
            "topic": "news",
            "search_depth": "basic",
            "max_results": self.max_results,
            "include_raw_content": "text",
            "include_published_date": True,
        }
        if self.exclude:
            body["exclude_domains"] = list(self.exclude)
        if window:
            body["start_date"] = window[0].isoformat()
            body["end_date"] = window[1].isoformat()
        status, data, text = self._call(body)
        if status in (400, 422):
            # A renamed or rejected option must not switch reading off: retry
            # once with the bare minimum, and say so in the problems list.
            minimal = {"query": query, "max_results": self.max_results,
                       "include_raw_content": True}
            status, data, text = self._call(minimal)
            if status == 200 and isinstance(data, dict):
                data["_degraded"] = f"first request rejected; retried minimal ({text[:120]})"
        if status == 200 and isinstance(data, dict):
            arts = [to_article(r) for r in data.get("results") or []]
            arts = [a for a in arts if not excluded(a["site"], self.exclude)]
            for a in arts:
                if data.get("_degraded"):
                    a["_degraded"] = data["_degraded"]
            return arts
        if status == 401:
            self.disabled = "bad_key"
            raise DeepUnavailable("bad_key", "Tavily rejected the key (401)")
        if status in (432, 433):
            self.disabled = "out_of_credits"
            raise DeepUnavailable("out_of_credits", f"Tavily plan limit reached ({status})")
        if status == 429:
            self.disabled = "rate_limited"
            raise DeepUnavailable("rate_limited", "Tavily rate limit (429)")
        raise DeepUnavailable("error", f"HTTP {status}: {text[:160]}")

    def _call(self, body):
        self.calls += 1
        try:
            return self._post(f"{API}/search", body, self._headers())
        except Exception as exc:                           # noqa: BLE001
            return 0, None, f"{type(exc).__name__}: {exc}"


def _http_post(url, body, headers):
    r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
    try:
        data = r.json()
    except ValueError:
        data = None
    return r.status_code, data, r.text


def _http_get(url, headers):
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    try:
        data = r.json()
    except ValueError:
        data = None
    return r.status_code, data, r.text


# ---------------------------------------------------------------------------
# Quote verification -- the anti-fabrication check
# ---------------------------------------------------------------------------

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                         "–": "-", "—": "-", " ": " "})


def normalise(text: str) -> str:
    """Lower case, letters and digits only, single spaces. Wording must match;
    punctuation, curly quotes and spacing may not."""
    t = (text or "").translate(_QUOTES).lower()
    return " ".join(re.sub(r"[^0-9a-z]+", " ", t).split())


def quote_found(quote: str, text: str, min_words: int) -> bool:
    """
    True if the quote appears verbatim (after normalise) in the text and is at
    least `min_words` long. Words, not characters: "Treasury Department on
    Thursday bought" is 38 characters yet says nothing about WHAT was bought.
    """
    q = normalise(quote)
    return len(q.split()) >= min_words and q in normalise(text)
