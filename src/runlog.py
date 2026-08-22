"""
The daily log.

This is written for YOU, not for the machine. Long prose belongs here rather
than in the CSVs, which stay tidy and tabular. When you test the system you
will want to read not just the questions but every decision that produced them
-- which agents fired, what they proposed, what was rejected and why.

That is also how you find redundant agents. questions.csv only shows survivors,
so it can never tell you that an agent has proposed forty questions and won
nothing. This file and proposals.csv can.

WHAT CHANGED IN STAGE TWO
-------------------------
Originally save() was called only at the end of a successful run. Any exception
before that point meant NO LOG FILE AT ALL -- the traceback went to stdout and
the markdown was never created. That is exactly the run you would want to send
to someone for diagnosis. Now:

  * every write is flushed to disk immediately, so a crash keeps everything
    written up to that moment
  * error() captures the exception and full traceback INTO the markdown
  * run.py wraps the pipeline in try/finally so save() always happens
"""

from __future__ import annotations

import datetime as dt
import traceback
from pathlib import Path

from . import config


class RunLog:
    def __init__(self, today: dt.date):
        self.today = today
        self.lines: list[str] = []
        self.path: Path = config.LOGS / f"{today.isoformat()}.md"
        self._started = False
        self._prefix = ""
        self.error_count = 0
        self.flag_count = 0
        self.warn_count = 0

    # -- writing -------------------------------------------------------------

    def heading(self, text: str) -> None:
        self.lines.append("")
        self.lines.append(f"## {text}")
        self.lines.append("")
        print(f"\n=== {text}")
        self._flush()

    def sub(self, text: str) -> None:
        self.lines.append("")
        self.lines.append(f"### {text}")
        self.lines.append("")
        print(f"\n-- {text}")
        self._flush()

    def info(self, text: str) -> None:
        self.lines.append(text)
        print(text)
        self._flush()

    def warn(self, text: str) -> None:
        self.warn_count += 1
        self.lines.append(f"**WARNING:** {text}")
        print(f"WARNING: {text}")
        self._flush()

    def block(self, text: str) -> None:
        self.lines.append("")
        self.lines.append("```")
        self.lines.append(text)
        self.lines.append("```")
        self.lines.append("")
        self._flush()

    def flag(self, text: str) -> None:
        """Something you should actually look at."""
        self.flag_count += 1
        self.lines.append("")
        self.lines.append(f"> **FLAGGED FOR YOUR ATTENTION:** {text}")
        self.lines.append("")
        print(f"\n*** FLAGGED: {text}\n")
        self._flush()

    def error(self, where: str, exc: BaseException) -> None:
        """
        Record a caught exception with its full traceback.

        Stages call this rather than letting the exception escape, so one
        broken stage cannot take the rest of the run down with it.
        """
        self.error_count += 1
        tb = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        self.lines.append("")
        self.lines.append(f"> **ERROR in {where}:** `{type(exc).__name__}: {exc}`")
        self.lines.append("")
        self.lines.append("<details><summary>traceback</summary>")
        self.lines.append("")
        self.lines.append("```")
        self.lines.append(tb.rstrip())
        self.lines.append("```")
        self.lines.append("")
        self.lines.append("</details>")
        self.lines.append("")
        print(f"\n!!! ERROR in {where}: {type(exc).__name__}: {exc}\n{tb}")
        self._flush()

    # -- finishing -----------------------------------------------------------

    def _header(self) -> list[str]:
        return [
            f"# Run log -- {self.today.isoformat()}",
            "",
            f"_Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
            "",
        ]

    def _flush(self) -> None:
        """
        Write everything so far to disk.

        Rewriting the whole file each time is wasteful but the file is small
        and this is the only approach that survives a hard crash, a timeout, or
        a cancelled GitHub Action.
        """
        try:
            self.save()
        except OSError:
            # Never let a logging failure break the run.
            pass

    def save(self) -> None:
        config.LOGS.mkdir(parents=True, exist_ok=True)
        existing = ""
        if not self._started:
            # First write of this run. If a log for today already exists from an
            # earlier run, keep it and append below a separator rather than
            # silently overwriting it.
            if self.path.exists():
                existing = self.path.read_text(encoding="utf-8")
                existing += (
                    "\n\n---\n\n"
                    f"_Later run, {dt.datetime.now().strftime('%H:%M:%S')}_\n"
                )
                self._prefix = existing
            else:
                self._prefix = "\n".join(self._header())
            self._started = True

        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(self._prefix)
            fh.write("\n".join(self.lines))
            fh.write("\n")

    def finalise(self) -> None:
        """Called once at the very end, successful or not."""
        self.lines.append("")
        self.lines.append("---")
        self.lines.append(
            f"_Run ended {dt.datetime.now().strftime('%H:%M:%S')} -- "
            f"{self.error_count} errors, {self.warn_count} warnings, "
            f"{self.flag_count} flags._"
        )
        self._flush()
        print(f"\nLog written to {self.path}")
