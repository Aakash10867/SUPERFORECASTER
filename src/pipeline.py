"""
The daily run, in order:

  0. Human overrides (run FIRST, so the portfolio gate cannot fill the last
     slot before your override arrives)
  1. Read new PDFs from inbox, skipping any already seen by content hash
  2. Structural filter, then model triage -> macro-relevant articles
  3. Deduplicate articles across papers (the same-wire-story problem)
  4. Each system's agents propose questions
  5. Within-system contest -> each system's best, or nothing
  6. Portfolio gate -> dedup, tag caps, space
  7. Write everything, log everything
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import config, store
from .agents import Proposal, dedupe_proposals, relevant_articles, run_agent
from .contest import run_contest
from .dedupe import Similarity, cluster_articles
from .extract import load_paper
from .gate import PortfolioGate, bucket_report, concentration_report
from .lexicon import Lexicon
from .models import ModelRouter
from .runlog import RunLog
from .triage import triage_paper


def run(today: dt.date | None = None, dry_run: bool = False) -> int:
    today = today or dt.date.today()
    log = RunLog(today)

    settings = config.load_settings()
    models_cfg = config.load_models()
    agents_cfg = config.load_agents()

    store.ensure_files()

    log.heading("Setup")
    router = ModelRouter(settings, models_cfg, log)
    sim = Similarity(router, log)
    lex = Lexicon(router, settings, sim, log)
    log.info(f"Lexicon loaded: {len(lex.tags)} approved tags")

    open_questions = store.open_questions()
    log.info(f"Portfolio: {len(open_questions)} open questions")

    # ---------------------------------------------------------------- 0
    admitted_by_human = _apply_overrides(open_questions, today, log, dry_run)

    # ---------------------------------------------------------------- 1
    log.heading("Reading papers")
    papers, articles = _read_inbox(router, settings, today, log, dry_run)

    if not articles:
        log.info("No new articles found.")
        _finish(log, router, open_questions, settings)
        return 0

    # ---------------------------------------------------------------- 3
    log.heading("Deduplicating stories across papers")
    articles = cluster_articles(
        articles, sim, settings["dedupe"]["article_threshold"], log
    )
    log.info(f"{len(articles)} distinct stories going to the agents")

    papers_desc = ", ".join(sorted({p.paper_guess for p in papers})) or "your newspapers"

    # Each system needs to know which domains belong to the OTHER systems, so
    # it can leave their stories alone. Without this, the Fed question was
    # proposed by both global macro and US politics on the same day, and the
    # genuine US politics stories never got a hearing.
    systems_cfg = agents_cfg.get("systems") or {}
    domain_briefs = {
        key: f"{cfg['name']}: {' '.join(cfg['brief'].split())[:200]}"
        for key, cfg in systems_cfg.items() if cfg.get("active", True)
    }

    # ---------------------------------------------------------------- 4 & 5
    log.heading("Systems and agents")
    all_proposals: list[Proposal] = []
    winners: list[Proposal] = []
    seq = [0]

    for system_key, system_cfg in systems_cfg.items():
        if not system_cfg.get("active", True):
            continue

        log.sub(system_cfg["name"])
        other_domains = "\n".join(
            f"- {text}" for key, text in domain_briefs.items() if key != system_key
        )
        subset = relevant_articles(articles, system_key)
        if not subset:
            log.info("  no relevant articles today")
            continue
        log.info(f"  {len(subset)} articles in scope")

        system_proposals: list[Proposal] = []
        for shape_key in system_cfg.get("agents", []):
            shape_cfg = (agents_cfg.get("shapes") or {}).get(shape_key)
            if not shape_cfg or not shape_cfg.get("active", True):
                continue
            proposals = run_agent(
                router, system_key, system_cfg, shape_key, shape_cfg,
                subset, settings, papers_desc, today, log, seq,
                other_domains=other_domains,
            )
            system_proposals.extend(proposals)

        if not system_proposals:
            log.info("  nothing proposed")
            continue

        for p in system_proposals:
            log.info(f"    [{p.agent}] {p.question}")

        # Several agents reading one story will often produce one question in
        # four wordings. Collapse those so the judge compares real options.
        all_proposals.extend(system_proposals)
        distinct = dedupe_proposals(
            system_proposals, sim, settings["dedupe"]["question_threshold"], log
        )

        winner, exceptional = run_contest(
            router, system_key, system_cfg, distinct,
            papers_desc, today, log, other_domains=other_domains,
        )
        if winner:
            winners.append(winner)

    # ---------------------------------------------------------------- 6
    log.heading("Portfolio gate")
    if not winners:
        log.info("No system produced a winner today. Nothing added.")
    gate = PortfolioGate(router, settings, sim, lex, log)
    admitted, deferred = gate.admit(winners, open_questions, today)

    # ---------------------------------------------------------------- 7
    if not dry_run:
        for p in all_proposals:
            store.append_row(config.PROPOSALS_CSV, p.as_row())

        for p in admitted:
            qid = store.next_question_id()
            store.append_row(config.QUESTIONS_CSV, _to_question(p, qid, today))
            log.info(f"  ADDED {qid}: {p.question}")

        _park_deferred(deferred, today, settings, log)
        _expire_waiting(today, settings, log)

    else:
        for p in admitted:
            log.info(f"  WOULD ADD: {p.question}")

    # ---------------------------------------------------------------- report
    log.heading("Portfolio after this run")
    current = store.open_questions() if not dry_run else open_questions
    log.info(f"Open questions: {len(current)} / {settings['portfolio']['max_open_questions']}")
    log.info("Buckets:")
    log.info(bucket_report(current, settings))
    log.info("Tag exposure:")
    log.info(concentration_report(current))

    if admitted_by_human:
        log.info("")
        log.info(
            f"{len(admitted_by_human)} question(s) admitted by human override this run. "
            "Note that overrides bypass tag caps -- check the exposure figures above."
        )

    _flag_exceptional(deferred, log)
    _finish(log, router, current, settings)
    return len(admitted)


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def _read_inbox(router, settings, today, log, dry_run):
    already = {r["fingerprint"] for r in store.read_rows(config.PROCESSED_CSV)}
    pdfs = sorted(config.INBOX.glob("*.pdf")) + sorted(config.INBOX.glob("*.PDF"))

    if not pdfs:
        log.info("Inbox is empty.")
        return [], []

    papers, articles = [], []
    for path in pdfs:
        paper = load_paper(path, settings)
        if paper is None:
            log.warn(f"Could not read {path.name} -- no extractable text")
            continue

        if paper.fingerprint in already:
            log.info(f"Skipping {path.name} -- already read (content matches a previous run)")
            continue

        found = triage_paper(router, paper, settings, log)
        papers.append(paper)
        articles.extend(found)

        if not dry_run:
            store.append_row(config.PROCESSED_CSV, {
                "fingerprint": paper.fingerprint,
                "filename": path.name,
                "paper_guess": paper.paper_guess,
                "issue_date_guess": paper.issue_date_guess,
                "pages": len(paper.pages),
                "processed_on": today.isoformat(),
                "articles_kept": len(found),
            })

    return papers, articles


def _apply_overrides(open_questions, today, log, dry_run) -> list[str]:
    """
    Promote proposals you have chosen by hand straight into the portfolio,
    bypassing every gate and cap.

    This runs before anything else so the day's new proposals cannot take the
    slot you wanted. A typo must fail loudly -- an override that silently does
    nothing is the worst possible outcome.
    """
    overrides = store.read_overrides()
    if not overrides:
        return []

    log.heading("Human overrides")
    proposals = {r["proposal_id"]: r for r in store.read_rows(config.PROPOSALS_CSV)}
    added = []

    for row in overrides:
        pid = row["proposal_id"].strip()
        note = (row.get("note") or "").strip()
        if pid not in proposals:
            raise SystemExit(
                f"\nOVERRIDE ERROR: proposal_id '{pid}' in config/overrides.csv "
                f"does not exist in data/proposals.csv.\n"
                "Copy the id exactly rather than retyping it. Nothing has been "
                "changed; fix the file and run again.\n"
            )

        p = proposals[pid]
        qid = store.next_question_id()
        log.info(f"  ADMITTING {pid} -> {qid} (bypassing all gates and caps)")
        log.info(f"    {p['question']}")
        log.info(f"    your note: {note or '(none)'}")

        if not dry_run:
            store.append_row(config.QUESTIONS_CSV, {
                "id": qid,
                "question": p["question"],
                "domain": p["system"],
                "bucket": p["bucket"],
                "created": today.isoformat(),
                "deadline": p["deadline"],
                "primary_tag": p["proposed_primary_tag"],
                "secondary_tags": p["proposed_secondary_tags"],
                "tertiary_tags": p["proposed_tertiary_tags"],
                "source": p["source"],
                "reasoning_value": p["reasoning_value"],
                "significance": p.get("significance", ""),
                "resolution_criteria": p["resolution_criteria"],
                "resolution_source": p["resolution_source"],
                "status": "open",
                "admitted_by": "human",
                "admission_note": note,
            })
            open_questions.append({
                "id": qid,
                "question": p["question"],
                "primary_tag": p["proposed_primary_tag"],
                "secondary_tags": p["proposed_secondary_tags"],
                "tertiary_tags": p["proposed_tertiary_tags"],
                "bucket": p["bucket"],
                "status": "open",
            })
        added.append(qid)

    if not dry_run:
        store.clear_overrides()
    return added


def _to_question(p: Proposal, qid: str, today: dt.date) -> dict:
    return {
        "id": qid,
        "question": p.question,
        "domain": p.system,
        "bucket": p.bucket,
        "created": today.isoformat(),
        "deadline": p.deadline,
        "primary_tag": p.proposed_primary_tag,
        "secondary_tags": p.proposed_secondary_tags,
        "tertiary_tags": p.proposed_tertiary_tags,
        "source": p.source,
        "reasoning_value": p.reasoning_value,
        "significance": p.significance,
        "resolution_criteria": p.resolution_criteria,
        "resolution_source": p.resolution_source,
        "status": "open",
        "resolved_date": "",
        "outcome": "",
        "brier": "",
        "admitted_by": "system",
        "admission_note": "",
    }


def _park_deferred(deferred, today, settings, log):
    """
    A good question rejected only for lack of space is still good tomorrow, so
    it waits rather than being discarded. Flagged questions wait longer,
    because big events resolve slowly and slots open up.
    """
    if not deferred:
        return
    normal = settings["waiting_list"]["normal_shelf_life_days"]
    flagged = settings["waiting_list"]["flagged_shelf_life_days"]
    for p in deferred:
        is_flagged = p.flagged_exceptional == "yes"
        life = flagged if is_flagged else normal
        store.append_row(config.WAITING_CSV, {
            "proposal_id": p.proposal_id,
            "added": today.isoformat(),
            "expires": (today + dt.timedelta(days=life)).isoformat(),
            "flagged_exceptional": p.flagged_exceptional,
            "question": p.question,
        })
    log.info(f"{len(deferred)} question(s) placed on the waiting list")


def _expire_waiting(today, settings, log):
    rows = store.read_rows(config.WAITING_CSV)
    if not rows:
        return
    keep, dropped = [], 0
    for r in rows:
        try:
            if dt.date.fromisoformat(r["expires"]) >= today:
                keep.append(r)
            else:
                dropped += 1
        except (ValueError, KeyError):
            keep.append(r)
    if dropped:
        store.rewrite(config.WAITING_CSV, keep)
        log.info(f"{dropped} waiting-list question(s) expired -- the news that "
                 "inspired them has moved on")


def _flag_exceptional(deferred, log):
    for p in deferred:
        if p.flagged_exceptional == "yes":
            log.flag(
                f"A question the system judged EXCEPTIONAL was turned away for "
                f"lack of space or tag concentration:\n>\n"
                f"> **{p.question}**\n>\n"
                f"> Reason: {p.outcome_reason}\n>\n"
                f"> If you agree this is a genuine regime break rather than a loud "
                f"news day, paste `{p.proposal_id}` into `config/overrides.csv` "
                f"and it will be admitted on the next run, bypassing all caps."
            )


def _finish(log, router, open_questions, settings):
    log.heading("Model usage")
    log.info(router.stats.summary())
    log.info(f"Total calls this run: {router.stats.total_calls}")
    log.save()
