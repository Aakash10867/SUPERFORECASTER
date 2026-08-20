"""
Triage: cleaned page text -> a list of macro-relevant articles.

The structural filter in extract.py removes what is obviously not journalism.
Triage removes what IS journalism but is not macro-relevant -- sports reports,
restaurant reviews, celebrity news, local crime. A model can recognise those in
a newspaper it has never seen, where a rule based on section headers cannot.
That is where the generalisation to unfamiliar papers actually lives.

The instruction is deliberately biased toward LETTING THINGS THROUGH. A wasted
article costs a fraction of a cent. A dropped front-page story is a question
you will never ask, and you would never know you missed it.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

from .extract import Page, Paper, clean_page


@dataclass
class Article:
    paper: str
    issue_date: str
    page: int
    headline: str
    summary: str
    key_facts: str
    domains: str      # comma-separated domain keys this could belong to

    def as_context(self) -> str:
        return (
            f"[{self.paper}, {self.issue_date}, p.{self.page}]\n"
            f"HEADLINE: {self.headline}\n"
            f"SUMMARY: {self.summary}\n"
            f"KEY FACTS: {self.key_facts}"
        )


PROMPT = """You are reading one page of a newspaper and extracting articles that could matter for forecasting macro-level events.

RELEVANT means the article bears on any of these:
- Economic policy, central banks, inflation, growth, budgets, taxation, trade
- Government decisions, legislation, courts, regulation, elections
- War, conflict, diplomacy, sanctions, international relations
- Energy, agriculture, infrastructure and industrial policy
- Large corporate or financial events with policy or macroeconomic significance

NOT RELEVANT: sports, arts, food, travel, fashion, celebrity, television, local
crime with no policy angle, personal finance advice, health and lifestyle tips,
puzzles, obituaries, letters to the editor, advertising copy.

IMPORTANT: when you are unsure, INCLUDE the article. Missing a significant story
is far more costly than including a marginal one.

Also ignore anything that is clearly advertising or a legal notice that survived
earlier filtering.

For each relevant article, return:
- "headline": the headline, or a short accurate one if none is visible
- "summary": 2-3 sentences on what actually happened
- "key_facts": the specific dates, numbers, names, deadlines and quantities in
  the article. Be precise and dense here -- this is what later stages reason
  from. If the article mentions a scheduled date, a deadline, a target or a
  published figure, it MUST appear here.
- "domains": which of these the article belongs to, comma-separated, from:
  india_macro, global_macro, us_politics, geopolitics
  (an article may belong to more than one; use "" if none fit)

Return AT MOST 8 articles. If a page carries more, keep the most
macro-relevant ones -- an over-long response gets truncated and the whole page
is lost.

Keep "summary" to 2-3 sentences and "key_facts" to one dense sentence. Brevity
here is not cosmetic: it is what keeps the response inside the model's output
limit.

Return ONLY a JSON array. If nothing on this page is relevant, return [].

PAGE TEXT:
---
{page_text}
---"""


def triage_page(router, paper: Paper, page: Page, cleaned: str, log,
                depth: int = 0) -> list[Article]:
    """
    Extract articles from one cleaned page.

    If the call fails, the page is split in half and each half retried once.
    Dense broadsheet pages can carry a dozen stories, and the resulting JSON is
    long enough that some models truncate or choke on it. Asking for a bigger
    token budget does not help when a model has a hard output ceiling -- giving
    it less to read does.
    """
    prompt = PROMPT.format(page_text=cleaned[:60000])
    result, model = router.generate("triage", prompt, temperature=0.2,
                                    max_output_tokens=8192)

    if not result:
        words = cleaned.split()
        if depth < 1 and len(words) > 400:
            # Split on a paragraph boundary near the middle.
            halves = cleaned.split("\n\n")
            mid = len(halves) // 2
            first = "\n\n".join(halves[:mid])
            second = "\n\n".join(halves[mid:])
            log.info(
                f"  triage retry: splitting {paper.paper_guess} p.{page.number} "
                f"({len(words)} words) into two halves"
            )
            return (triage_page(router, paper, page, first, log, depth + 1)
                    + triage_page(router, paper, page, second, log, depth + 1))

        # Report what actually went wrong. "triage failed" on its own sends you
        # hunting for a prompt problem when the cause may be a server error.
        recent = []
        for errs in router.stats.failures_by_model.values():
            recent.extend(errs[-2:])
        reason = ", ".join(sorted(set(recent))[-3:]) or "unknown"
        log.warn(
            f"triage failed on {paper.paper_guess} p.{page.number} "
            f"({len(words)} words) -- recent model errors: {reason}"
        )
        return []

    if not isinstance(result, list):
        return []

    articles = []
    for item in result:
        if not isinstance(item, dict):
            continue
        headline = str(item.get("headline", "")).strip()
        summary = str(item.get("summary", "")).strip()
        if not headline and not summary:
            continue
        articles.append(Article(
            paper=paper.paper_guess,
            issue_date=paper.issue_date_guess,
            page=page.number,
            headline=headline,
            summary=summary,
            key_facts=str(item.get("key_facts", "")).strip(),
            domains=str(item.get("domains", "")).strip(),
        ))
    return articles


def triage_paper(router, paper: Paper, settings: dict, log) -> list[Article]:
    min_words = settings["filtering"]["min_page_words"]
    articles: list[Article] = []
    skipped = 0

    for page in paper.pages:
        cleaned = clean_page(page, settings)
        if len(cleaned.split()) < min_words:
            skipped += 1
            continue
        found = triage_page(router, paper, page, cleaned, log)
        articles.extend(found)

    log.info(
        f"{paper.paper_guess} ({paper.issue_date_guess or 'date unknown'}): "
        f"{len(paper.pages)} pages, {skipped} dropped by structural filter, "
        f"{len(articles)} articles kept"
    )
    return articles
