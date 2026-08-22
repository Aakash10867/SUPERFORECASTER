"""
The daily run, in order:

  0. Human overrides (run FIRST, so the portfolio gate cannot fill the last
     slot before your override arrives) -- plus config/resolutions.csv
  1. Read new PDFs from inbox, structural filter, model triage, cross-paper
     deduplication
  2. RESOLUTION: merged screen, confirmation, lapse, absence watch
  3. GENERATION: agents propose, contest, portfolio gate
  4. FORECASTING: seven lenses, aggregate, devil's advocate
  5. REPORTS: scoring recomputed from source, fast-clock diagnostics

WHY RESOLUTION COMES BEFORE GENERATION
--------------------------------------
Resolving a question frees a portfolio slot, so the same run can fill it.
Reversed, every resolution costs a day of portfolio capacity.

WHY FORECASTING COMES AFTER GENERATION
--------------------------------------
Questions created today get their first forecast today, giving a complete trail
from birth for the day-weighted score. This is not contamination: the
outside-view phase of every lens runs blind to news regardless, and the inside
view SHOULD see the story the question came from.

STAGE ISOLATION
---------------
Every stage is wrapped so a failure logs loudly -- with a traceback, into the
markdown log -- and the run continues. Nothing may block.

THE EARLY RETURN IS GONE
------------------------
The old code bailed out entirely when the inbox held no new articles. But the
weekly refresh is staleness-driven and needs no news, and deadline lapses need
no news either. Resolution, forecasting and reports now run regardless.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import config, forecast, lenses as lensmod, reports, resolve, store
from .agents import Proposal, dedupe_proposals, relevant_articles, run_agent
from .contest import run_contest
from .dedupe import Similarity, cluster_articles
from .extract import load_paper
from .gate import PortfolioGate, bucket_report, concentration_report
from .lexicon import Lexicon
from .models import ModelRouter
from .runlog import RunLog
from .triage import triage_paper


STAGE_NAMES = ["resolution", "generation", "forecasting", "reports"]


def run(today: dt.date | None = None, dry_run: bool = False,
        stages: str = "all") -> int:
    """
    Orchestrate the whole run. `stages` is "all" or a comma-separated subset of
    STAGE_NAMES, so the first run after replacing the repo can exercise
    generation alone before the forecasting code is in the path.
    """
    today = today or dt.date.today()
    log = RunLog(today)

    wanted = (
        set(STAGE_NAMES) if stages.strip().lower() in ("", "all")
        else {x.strip().lower() for x in stages.split(",") if x.strip()}
    )
    unknown = wanted - set(STAGE_NAMES)
    if unknown:
        raise SystemExit(
            f"Unknown stage(s): {', '.join(sorted(unknown))}. "
            f"Choose from: {', '.join(STAGE_NAMES)}, or 'all'."
        )

    settings = config.load_settings()
    models_cfg = config.load_models()
    agents_cfg = config.load_agents()

    store.ensure_files()

    log.heading("Setup")
    log.info(f"Stages this run: {', '.join(sorted(wanted))}")
    router = ModelRouter(settings, models_cfg, log)
    log.info(f"API keys in use: {', '.join(router.key_names)}")
    log.info("Quota already used today (persisted across runs):")
    log.info(router.quota.summary())
    sim = Similarity(router, log)
    lex = Lexicon(router, settings, sim, log)
    log.info(f"Lexicon loaded: {len(lex.tags)} approved tags")

    open_questions = store.open_questions()
    log.info(f"Portfolio: {len(open_questions)} open questions")

    added = 0
    try:
        # ------------------------------------------------------------ 0
        admitted_by_human = _apply_overrides(open_questions, today, log, dry_run)
        if not dry_run:
            log.heading("Human resolution corrections")
            n = resolve.apply_human_resolutions(log, today)
            log.info(f"  {n} correction(s) found in config/resolutions.csv")

        # ------------------------------------------------------------ 1
        log.heading("Reading papers")
        papers, articles = _read_inbox(router, settings, today, log, dry_run)

        if articles:
            log.heading("Deduplicating stories across papers")
            before_dedup = len(articles)
            articles = cluster_articles(
                articles, sim, settings["dedupe"]["article_threshold"], log
            )
            log.info(f"{len(articles)} distinct stories going forward")
            # A collapse ratio near 1.0 means deduplication did nothing, which
            # on live run 3 meant embeddings had died and lexical fallback was
            # missing everything. The visible damage was downstream --
            # duplicate proposals, and seven new tags invented that had been
            # correctly MERGED the run before -- so the cause needs saying
            # here, loudly, rather than being inferred later.
            if before_dedup >= 50 and len(articles) / before_dedup > 0.95:
                log.flag(
                    f"Deduplication collapsed only {before_dedup - len(articles)} "
                    f"of {before_dedup} articles. Similarity matching is "
                    "probably degraded -- check for embedding failures above. "
                    "Expect duplicate proposals and spurious new tags this run."
                )
        else:
            log.info(
                "No new articles found. Resolution, forecasting and reports "
                "still run -- staleness refreshes and deadline lapses do not "
                "depend on today's papers."
            )

        news_text = _news_digest(articles)

        # ------------------------------------------------------------ 2
        if "resolution" in wanted:
            try:
                _run_resolution(router, settings, today, log, dry_run, news_text)
            except Exception as exc:                       # noqa: BLE001
                log.error("resolution stage", exc)

        # ------------------------------------------------------------ 3
        if "generation" in wanted and articles:
            try:
                added = _run_generation(
                    router, sim, lex, settings, agents_cfg, articles, papers,
                    today, log, dry_run, store.open_questions(),
                    admitted_by_human,
                )
            except Exception as exc:                       # noqa: BLE001
                log.error("generation stage", exc)
        elif "generation" in wanted:
            log.heading("Generation")
            log.info("  skipped: no new articles to propose from")

        # ------------------------------------------------------------ 4
        if "forecasting" in wanted:
            try:
                _run_forecasting(router, sim, settings, today, log, dry_run,
                                 news_text)
            except Exception as exc:                       # noqa: BLE001
                log.error("forecasting stage", exc)

        # ------------------------------------------------------------ 5
        if "reports" in wanted and not dry_run:
            try:
                log.heading("Reports")
                reports.write_reports(today, log, settings)
            except Exception as exc:                       # noqa: BLE001
                log.error("reports stage", exc)
    finally:
        _finish(log, router, store.open_questions(), settings)

    return added


def _run_generation(router, sim, lex, settings, agents_cfg, articles, papers,
                    today, log, dry_run, open_questions, admitted_by_human):

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
    """
    Always runs, successful or not -- it sits in the orchestrator's `finally`.
    Quota is reported per key AND per day, because that is the limit that
    actually binds; the per-run figure is only interesting for spotting runaway
    loops.
    """
    log.heading("Model usage")
    log.info(router.stats.summary())
    log.info(f"Total calls this run: {router.stats.total_calls}")
    log.info("Quota used today, per key (this is the limit that binds):")
    log.info(router.quota.summary())
    try:
        deep_left = router._deep_remaining()
        log.info(f"Deep-model calls left today: {deep_left}")
        if deep_left <= 0:
            log.warn(
                "Deep-model quota is exhausted for today. Generation's contest "
                "step will fall back to lighter models until it resets."
            )
    except Exception:  # noqa: BLE001
        pass
    log.finalise()


# ---------------------------------------------------------------------------
# Stage 2: resolution
# ---------------------------------------------------------------------------

def _news_digest(articles, limit: int = 60) -> str:
    """
    A compact digest of everything read this run, for the screen and the
    inside-view stages.

    Deliberately covers EVERYTHING SINCE THE LAST RUN rather than "today".
    Papers arrive on weekdays only and runs are irregular, so a Monday run
    covers Friday through Monday. Framed as "today's news", weekend events fall
    into a gap and Monday's reporting of them looks like stale restatement.
    """
    if not articles:
        return ""
    bits = []
    for a in articles[:limit]:
        title = getattr(a, "headline", "") or getattr(a, "title", "")
        body = (getattr(a, "text", "") or "")[:700]
        paper = getattr(a, "paper", "") or getattr(a, "paper_guess", "")
        bits.append(f"[{paper}] {title}\n{body}")
    return "\n\n---\n\n".join(bits)


def _trigger_text(qid: str) -> str:
    """The triggers each lens declared last time, for the screen to match against."""
    rec = store.latest_run_record(qid)
    if not rec:
        return ""
    lines = []
    for lid, e in (rec.get("lenses") or {}).items():
        for t in (e.get("triggers") or [])[:2]:
            if isinstance(t, dict) and t.get("event"):
                lines.append(
                    f"- [{lid}] {t.get('event')} "
                    f"({t.get('direction','')} to ~{t.get('to_roughly','')})"
                )
    return "\n".join(lines)


def _run_resolution(router, settings, today, log, dry_run, news_text):
    log.heading("Resolution")
    grace = int(settings["portfolio"]["resolution_grace_days"])
    open_qs = store.open_questions()

    escalated: list[str] = []

    for q in open_qs:
        qid = q.get("id", "")
        result = resolve.screen_question(
            q, news_text, _trigger_text(qid),
            since="the last run", today=today, router=router,
        )
        if result is None:
            log.warn(f"  {qid}: screen returned nothing; treating as no cause")
            result = {"resolution_nominee": False, "escalate": False,
                      "reason": "screen call failed"}

        if not dry_run:
            store.append_row(config.SCREENS_CSV, {
                "question_id": qid,
                "date": today.isoformat(),
                "outcome": ("resolution_nominee"
                            if result.get("resolution_nominee")
                            else "escalate" if result.get("escalate")
                            else "no_cause"),
                "reason": result.get("reason", ""),
                "articles_considered": len(news_text.split("---")) if news_text else 0,
                "trigger_fired": result.get("trigger_fired", ""),
                "model": result.get("_model", ""),
            })

        log.info(f"  {qid}: {result.get('reason','(no reason given)')}")

        if result.get("resolution_nominee"):
            log.info(f"    nominated for resolution: {result.get('resolution_event','')}")
            ok, outcome, event_date, detail = resolve.confirm_resolution(
                q, result.get("resolution_event", ""), news_text, router, log
            )
            if ok and not dry_run:
                resolve.resolve_now(q, outcome, event_date, today, log)
                continue
            if not ok:
                log.info(
                    "    not confirmed -- the two confirmers did not both "
                    "agree that every clause was clearly met. Question stays "
                    "open; it will be screened again next run."
                )
        if result.get("escalate"):
            escalated.append(qid)

    # -- lapses ------------------------------------------------------------
    for q in store.open_questions():
        if resolve.lapse_due(q, today, grace) and not dry_run:
            resolve.lapse(q, today, settings, log)

    # -- the absence watch -------------------------------------------------
    watched = store.watched_questions()
    if watched:
        log.sub("Absence watch")
        log.info(
            f"  {len(watched)} question(s) lapsed for want of news are still "
            "being watched cheaply."
        )
        for q in watched:
            result = resolve.screen_question(
                q, news_text, "", since="the last run", today=today,
                router=router,
            )
            if result and result.get("resolution_nominee") and not dry_run:
                ok, outcome, event_date, _detail = resolve.confirm_resolution(
                    q, result.get("resolution_event", ""), news_text, router, log
                )
                if ok:
                    resolve.resolve_now(q, outcome, event_date, today, log,
                                        basis="confirmed_act")
                    log.flag(
                        f"{q['id']}: LATE REPORTING FOUND. The outcome and the "
                        f"resolved date have both changed, and every score has "
                        f"been recomputed from source."
                    )
        if not dry_run:
            resolve.expire_watches(today, log)

    if escalated:
        log.info(f"  escalated for a full re-forecast: {', '.join(escalated)}")
    _ESCALATED.clear()
    _ESCALATED.extend(escalated)


# Escalations are handed from the resolution stage to the forecasting stage.
# A module-level list rather than a return value, so that a failure in
# resolution cannot break forecasting's signature.
_ESCALATED: list[str] = []


# ---------------------------------------------------------------------------
# Stage 4: forecasting
# ---------------------------------------------------------------------------

def _run_forecasting(router, sim, settings, today, log, dry_run, news_text):
    log.heading("Forecasting")

    lenses_cfg = config.load_lenses()
    lens_defs = [l for l in lenses_cfg.get("lenses", []) if l.get("active", True)]
    config_version = str(lenses_cfg.get("version", "1"))
    log.info(f"  lens config version {config_version}: "
             f"{', '.join(l['id'] for l in lens_defs)}")

    fc = settings.get("forecasting", {})
    staleness = int(fc.get("staleness_days", 7))
    runner = lensmod.LensRunner(
        router, log, settings, config_version, sim,
        shared_ground=lenses_cfg.get("shared_ground", ""),
    )

    open_qs = store.open_questions()
    if not open_qs:
        log.info("  no open questions to forecast")
        return

    for q in open_qs:
        qid = q.get("id", "")
        shape = (q.get("shape") or "").strip()
        if shape not in ("window", "point"):
            # Stage one does not emit `shape`, so classify it here -- once per
            # question, then stored. On the first live run this defaulted
            # silently to `point` for two questions that were plainly windows,
            # which meant the three-horizon mechanism never ran at all.
            shape, why = forecast.classify_shape(q, router, log)
            if not dry_run:
                store.update_question(qid, {"shape": shape})
            log.info(f"  {qid}: classified as '{shape}' -- {why}")
            q["shape"] = shape

        never_forecast = not (q.get("last_refresh") or "").strip()
        stale = forecast.needs_refresh(q, today, staleness)
        escalated = qid in _ESCALATED
        is_point = shape != "window"

        # Point-event questions still refresh on staleness and on escalation;
        # what they skip is the three-horizon output, not the forecast itself.
        if not (never_forecast or stale or escalated):
            log.info(f"  {qid}: no cause and not stale; number persists flat")
            continue

        cause = ("creation" if never_forecast
                 else "trigger" if escalated else "refresh")
        log.sub(f"{qid} ({cause}, {shape})")

        prior_numbers = {}
        prev = store.latest_run_record(qid)
        if prev:
            for lid, e in (prev.get("lenses") or {}).items():
                if e.get("status") == "responded":
                    prior_numbers[lid] = e.get("probability")

        record = forecast.run_question(
            q, lens_defs, runner, router, log, settings, today,
            news_text, cause, similarity=sim,
            prior_lens_numbers=prior_numbers,
        )

        if record.get("status") == "forecast":
            # Report every terminal state. The first live run printed only
            # "responded" and "abstained", so three lenses excluded by the
            # audit vanished without appearing anywhere in the log.
            bits = [f"{record.get('responding_lenses')} produced a number"]
            for label, key in (("abstained", "abstained"),
                               ("fell back to outside view", "fallbacks"),
                               ("excluded by audit", "excluded"),
                               ("failed", "failed")):
                names = record.get(key) or []
                if names:
                    bits.append(f"{label}: {', '.join(names)}")
            log.info(
                f"  median {record.get('median_raw')} -> final "
                f"{record.get('probability')}  ({'; '.join(bits)})"
            )
            adv = record.get("advocate") or {}
            if adv.get("load_bearing_assumption"):
                log.info(f"  advocate (finding): "
                         f"{adv['load_bearing_assumption'][:300]}")
            if record.get("advocate_proposed") is not None:
                log.info(
                    f"  advocate would have said "
                    f"{record['advocate_proposed']} -- recorded as a shadow, "
                    "NOT applied"
                )
        if not dry_run:
            forecast.persist(record, q, today, log)
            reports.check_record(record, today, log)

    if not dry_run:
        div = reports.curve_divergence(today, log)
        if div:
            log.sub("Curve divergence")
            log.info(
                "  Last refresh declared where these numbers should sit today; "
                "today's refresh disagrees. The curve is a test instrument, "
                "not a value source -- persistent divergence means a lens's "
                "time model is wrong."
            )
            for d in div:
                log.info(
                    f"  {d['question_id']}/{d['lens']}: declared ~{d['declared']}, "
                    f"now {d['actual']}"
                )
