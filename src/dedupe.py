"""
Similarity: used in three places.

1. Article dedup across papers. When WaPo, WSJ and Mint all run the same wire
   story, the generator must not treat that as three independent signals.
2. Question near-duplicate detection (same question, different wording).
3. Finding the nearest existing tags before deciding whether a proposed tag is
   genuinely new.

Embeddings are used when available because they catch semantic similarity
("Iran war" vs "West Asia conflict"). If the embedding call fails for any
reason, we fall back to local lexical similarity, which is worse but free and
never unavailable. Falling back degrades quality; it never stops the run.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+")

_STOP = set("""
a an the and or but if then than that this these those of in on at to for from by
with about into over after before between under above is are was were be been being
will would shall should may might can could has have had do does did not no nor
it its it's as so such very more most other some any each which who whom whose what
when where why how all both few many much own same too also only just
""".split())


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 2]


def lexical_similarity(a: str, b: str) -> float:
    """Cosine similarity over term-frequency vectors. Cheap, local, no API."""
    ta, tb = Counter(_tokens(a)), Counter(_tokens(b))
    if not ta or not tb:
        return 0.0
    shared = set(ta) & set(tb)
    if not shared:
        return 0.0
    dot = sum(ta[t] * tb[t] for t in shared)
    na = math.sqrt(sum(v * v for v in ta.values()))
    nb = math.sqrt(sum(v * v for v in tb.values()))
    return dot / (na * nb) if na and nb else 0.0


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Similarity:
    """Wraps the router so callers do not have to think about the fallback."""

    def __init__(self, router, log):
        self.router = router
        self.log = log
        self._embeddings_ok = True

    def rank(self, query: str, candidates: list[str]) -> list[tuple[int, float]]:
        """
        Return [(index_into_candidates, score), ...] sorted best first.
        """
        if not candidates:
            return []

        if self._embeddings_ok:
            vecs = self.router.embed([query] + candidates)
            if vecs and len(vecs) == len(candidates) + 1:
                q = vecs[0]
                scored = [(i, cosine(q, v)) for i, v in enumerate(vecs[1:])]
                return sorted(scored, key=lambda kv: -kv[1])
            self._embeddings_ok = False
            self.log.warn(
                "Embeddings unavailable; falling back to lexical similarity. "
                "Semantic matches (e.g. 'Iran war' vs 'West Asia conflict') "
                "may be missed."
            )

        scored = [(i, lexical_similarity(query, c)) for i, c in enumerate(candidates)]
        return sorted(scored, key=lambda kv: -kv[1])

    def best_match(self, query: str, candidates: list[str]) -> tuple[int, float]:
        ranked = self.rank(query, candidates)
        return ranked[0] if ranked else (-1, 0.0)


def cluster_articles(articles, sim: Similarity, threshold: float, log) -> list:
    """
    Collapse articles covering the same story into one representative, keeping a
    note of which papers carried it.

    Agreement across your papers is weaker evidence than it looks -- three
    outlets running the same wire copy is one source, not three. Recording how
    many papers carried a story preserves that information for later stages
    without letting it be mistaken for independent confirmation.
    """
    if not articles:
        return []

    texts = [f"{a.headline}. {a.summary}" for a in articles]
    used = set()
    clusters = []

    for i, art in enumerate(articles):
        if i in used:
            continue
        group = [art]
        used.add(i)
        remaining_idx = [j for j in range(i + 1, len(articles)) if j not in used]
        if remaining_idx:
            ranked = sim.rank(texts[i], [texts[j] for j in remaining_idx])
            for pos, score in ranked:
                if score < threshold:
                    break
                j = remaining_idx[pos]
                if j not in used:
                    group.append(articles[j])
                    used.add(j)
        clusters.append(group)

    merged = []
    for group in clusters:
        primary = max(group, key=lambda a: len(a.key_facts or ""))
        papers = sorted({g.paper for g in group})
        if len(papers) > 1:
            primary.summary += f" [Also carried by: {', '.join(papers)}]"
        merged.append(primary)

    if len(merged) < len(articles):
        log.info(
            f"Article dedup: {len(articles)} articles -> {len(merged)} distinct stories"
        )
    return merged
