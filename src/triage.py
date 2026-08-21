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

CRITICAL -- NEVER EXTRACT HISTORICAL REPRINTS. Newspapers routinely reproduce
their own old front pages and run retrospective features. Mint prints a
"10 YEARS AGO" panel; other papers run "On This Day", "From the Archives",
anniversary retrospectives and decade-in-review pieces.

These read exactly like current news and are not. A story from such a section
will produce a forecasting question about something that already happened years
ago. Skip them entirely.

Signs you are looking at one: a section heading mentioning years ago or
archives; a dateline whose year is not the current year; a masthead or volume
number inside the body text; references to officials or prices that belong to a
different period; the same headline styled as a reproduced page.

If a passage carries a date more than three months before this paper's own
issue date, treat it as historical unless it is clearly reporting on a past
event as background to current news.

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

    IMPORTANT: an empty list means "this page genuinely has no macro-relevant
    news" -- a correct and very common answer, since papers are full of sport,
    comics, food and listings. Only `None` means the call failed.

    An earlier version tested `if not result`, which is true for BOTH. Every
    page that correctly contained nothing was logged as a failure and then
    split and retried, wasting about forty calls a run and filling the log with
    warnings about the system working properly.
    """
    prompt = PROMPT.format(page_text=cleaned[:60000])
    result, model = router.generate("triage", prompt, temperature=0.2,
                                    max_output_tokens=8192)

    if result is None:
        words = cleaned.split()
        if depth < 1 and len(words) > 400:
            halves = cleaned.split("\n\n")
            mid = len(halves) // 2
            first = "\n\n".join(halves[:mid])
            second = "\n\n".join(halves[mid:])
            log.info(
                f"  triage retry: splitting {paper.paper_guess} p.{page.number} "
                f"({len(words)} words) after a failed call"
            )
            return (triage_page(router, paper, page, first, log, depth + 1)
                    + triage_page(router, paper, page, second, log, depth + 1))

        log.warn(
            f"triage call failed on {paper.paper_guess} p.{page.number} "
            f"({len(words)} words) -- last error: {router.stats.last_error or 'unknown'}"
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
    no_news = 0

    for page in paper.pages:
        cleaned = clean_page(page, settings)
        if len(cleaned.split()) < min_words:
            skipped += 1
            continue
        found = triage_page(router, paper, page, cleaned, log)
        if not found:
            no_news += 1
        articles.extend(found)

    log.info(
        f"{paper.paper_guess} ({paper.issue_date_guess or 'date unknown'}): "
        f"{len(paper.pages)} pages | {skipped} dropped as non-journalism | "
        f"{no_news} read but held no macro news | {len(articles)} articles kept"
    )
    return articles
