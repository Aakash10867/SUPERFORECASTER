#!/usr/bin/env python3
"""
Do the tests actually protect anything? (v22)

Passing tests prove nothing if they would ALSO pass on broken code. Up to
v21, several did: a backfill test that could never trigger, a "same publisher
twice" test that never reached de-duplication, a "born resolved" test that
passed for an unrelated reason.

This script breaks ONE safety guard at a time in a throwaway copy of the repo
and runs test_web_resolve.py. Every sabotage must make the tests FAIL. A
"MISSED!" line means a guard has no real test -- fix the test before
trusting the build.

    python test_guards.py
"""
import shutil, subprocess, sys, tempfile
from pathlib import Path
SRC = Path(__file__).resolve().parent
MUTANTS = [
  ("quote check disabled (fabricated quotes accepted)", "src/deep_search.py",
   "return len(q.split()) >= min_words and q in normalise(text)", "return True"),
  ("'published before the event' check removed", "src/web_resolve.py",
   "if pub is not None and pub < event_date:", "if False:"),
  ("headlines treated as full text (tier C -> B)", "src/web_resolve.py",
   'tier="A" if (kind == "T" and official) else "B" if kind == "T" else "C",',
   'tier="A" if (kind == "T" and official) else "B",'),
  ("every site treated as official", "src/web_resolve.py",
   "official = _is_official(site, settings)", "official = True"),
  ("same publisher counted twice", "src/web_resolve.py",
   'if v["site"] and v["site"] not in sites:', 'if v["site"]:'),
  ("undated non-official articles accepted", "src/web_resolve.py",
   'if pub is None and not (kind == "T" and official):', "if False:"),
  ("backfill guard removed", "src/web_resolve.py",
   "if today != real_today:", "if False:"),
  ("Tavily keeps calling after out-of-credits", "src/deep_search.py",
   "        if self.disabled:\n            raise DeepUnavailable(self.disabled, \"disabled earlier this run\")",
   "        pass"),
  ("weekly sweep fires every day (credit leak)", "src/web_resolve.py",
   "if last is None or (today - last).days >= every:", "if True:"),
  ("born-resolved check removed", "src/web_resolve.py",
   "if created and event_date < created:", "if False:"),
  ("corroboration accepted without re-judging", "src/web_resolve.py",
   'out.update(action=action, why=why, facts=new_facts, answer=merged)',
   'out.update(action="resolved_yes", why=why, facts=dict(new_facts, event_date=facts.get("event_date")), answer=merged)'),
  ("corroboration REPLACES the first evidence instead of adding", "src/web_resolve.py",
   'merged["evidence"] = list(answer.get("evidence") or []) + extra',
   'merged["evidence"] = extra'),
  ("failed corroboration crashes the run", "src/web_resolve.py",
   '        out["note"] = "corroboration call gave no answer; first judgement kept"\n        return out',
   '        raise RuntimeError("corroboration failed")'),
  ("social sites not filtered in code", "src/deep_search.py",
   'arts = [a for a in arts if not excluded(a["site"], self.exclude)]',
   'arts = arts'),
  ("no-key alert removed (silent failure)", "src/web_resolve.py",
   '_alert(log, "FULL-TEXT CHECK OFF: the TAVILY_API_KEY secret is not set. "',
   'log.info("FULL-TEXT CHECK OFF: the TAVILY_API_KEY secret is not set. "'),
]
caught = 0
for name, rel, old, new in MUTANTS:
    tmp = Path(tempfile.mkdtemp(prefix="sf-guard-")) / "repo"
    shutil.copytree(SRC, tmp, ignore=shutil.ignore_patterns(
        "__pycache__", "data", "logs", "inbox", ".git"))
    f = tmp / rel
    s = f.read_text()
    assert old in s, f"mutation anchor missing: {name}"
    f.write_text(s.replace(old, new, 1))
    res = subprocess.run([sys.executable, "test_web_resolve.py"], cwd=tmp,
                         capture_output=True, text=True)
    fails = [l.strip() for l in res.stdout.splitlines() if "[FAIL]" in l]
    ok = res.returncode != 0
    caught += ok
    print(f"{'CAUGHT ' if ok else 'MISSED!'} {name}  ->  {fails[0][7:80] if fails else res.stderr.strip()[-120:]}")
    shutil.rmtree(tmp.parent, ignore_errors=True)
print(f"\n{caught}/{len(MUTANTS)} sabotages caught by the tests")
sys.exit(0 if caught == len(MUTANTS) else 1)
