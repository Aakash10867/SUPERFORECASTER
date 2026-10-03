"""
News search via Google News RSS (v18).

WHY THIS REPLACED GEMINI'S BUILT-IN SEARCH
------------------------------------------
v16/v17 relied on Gemini's "grounding with Google Search". On these API keys
the only models with a free search allowance are Gemini 2.5 Flash and
2.5 Flash-Lite, and Google has withdrawn both for NEW API keys: they still
appear in the model list and the quota table, but every call returns HTTP 404
"no longer available to new users". Every Gemini 3.x model has a search
allowance of zero. So on 1-3 Oct the web check made no searches at all, and
Q0003 was never looked up.

THE NEW SPLIT: CODE SEARCHES, THE MODEL ONLY READS
--------------------------------------------------
Google News publishes search results as a free RSS feed: no key, no quota.
This module fetches it and returns, for each article, the headline, the
publisher, the publisher's site and the publication date -- all taken from the
feed itself. A model (3.x Flash Lite, 500 calls/day) then reads that list. It
cannot invent a source, because the list of sources comes from here, not from
anything the model writes.

What it gives up: the feed carries headlines and a line of snippet, not full
articles. Telling "Treasury TO buy $6bn" from "Treasury BOUGHT $6bn" rests on
headline wording, so borderline cases go to you rather than resolving.

TWO EDITIONS
------------
Every query is run against the US and the India editions of Google News. The
portfolio straddles both, and a story like the Ganga treaty is covered far
better by the India edition. Results are merged and de-duplicated.

FAILURE IS LOUD
---------------
If no fetch succeeds at all (network, block, changed feed), search() raises
SearchUnavailable. The caller turns that into a red alert at the top of the
run log -- never a quiet warning in the middle, which is how the v16/v17
failure went unnoticed.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse

import requests

FEED = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"
EDITIONS = (
    ("en-US", "US", "US:en"),
    ("en-IN", "IN", "IN:en"),
)
HEADERS = {
    # A plain browser-like agent; the feed refuses some default library agents.
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
}
TIMEOUT = 25
PAUSE = 1.0          # seconds between fetches, to stay polite


class SearchUnavailable(RuntimeError):
    """No fetch succeeded at all -- the web check cannot run this time."""


def _strip_tags(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


def _site(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def parse(xml_text: str) -> list[dict]:
    """
    Parse one RSS response into article dicts:
      {"title", "publisher", "site", "date" (YYYY-MM-DD, UTC), "snippet", "link"}
    Items without a parseable date are dropped: the date checks need one.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        src = item.find("source")
        publisher = (src.text or "").strip() if src is not None else ""
        site = _site(src.get("url", "")) if src is not None else ""
        # Google appends " - Publisher" to every headline; drop it.
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(" - " + publisher)].strip()
        try:
            d = parsedate_to_datetime(item.findtext("pubDate") or "")
            if d.tzinfo is not None:
                d = d.astimezone(dt.timezone.utc)
            date = d.date().isoformat()
        except (TypeError, ValueError, IndexError):
            continue
        snippet = _strip_tags(item.findtext("description") or "")
        if snippet.startswith(title):
            snippet = snippet[len(title):].strip(" -")
        if publisher and snippet.endswith(publisher):
            snippet = snippet[: -len(publisher)].strip(" -")
        out.append({
            "title": title,
            "publisher": publisher or site or "unknown",
            "site": site or (publisher or "").lower(),
            "date": date,
            "snippet": snippet[:300],
            "link": (item.findtext("link") or "").strip(),
        })
    return out


def search(queries: list[str], limit: int = 25, fetch=None) -> tuple[list[dict], list[str]]:
    """
    Run every query against both editions; return (articles, problems).

    Articles are merged, de-duplicated by headline, newest first, capped at
    `limit`. `problems` lists fetches that failed -- partial failure is
    reported but tolerated. If EVERY fetch fails, raises SearchUnavailable.

    `fetch(url) -> (status_code, text)` is injectable for offline tests.
    """
    fetch = fetch or _http_get
    seen, articles, problems = set(), [], []
    attempts = ok = 0
    for query in [q for q in queries if q and q.strip()][:3]:
        for hl, gl, ceid in EDITIONS:
            url = FEED.format(q=quote_plus(query.strip()), hl=hl, gl=gl, ceid=ceid)
            attempts += 1
            try:
                status, text = fetch(url)
            except Exception as exc:                       # noqa: BLE001
                problems.append(f"{gl} '{query}': {type(exc).__name__}: {exc}")
                continue
            if status != 200:
                problems.append(f"{gl} '{query}': HTTP {status}")
                continue
            ok += 1
            for a in parse(text):
                key = re.sub(r"\W+", " ", a["title"].lower()).strip()
                if key and key not in seen:
                    seen.add(key)
                    articles.append(a)
            if fetch is _http_get:
                time.sleep(PAUSE)
    if attempts and not ok:
        raise SearchUnavailable("; ".join(problems[:4]) or "no fetch succeeded")
    articles.sort(key=lambda a: a["date"], reverse=True)
    return articles[:limit], problems


def _http_get(url: str) -> tuple[int, str]:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    return r.status_code, r.text
