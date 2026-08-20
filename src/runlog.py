"""
The daily log.

This is written for YOU, not for the machine. Long prose belongs here rather
than in the CSVs, which stay tidy and tabular. When you test the system you
will want to read not just the questions but every decision that produced them
-- which agents fired, what they proposed, what was rejected and why.

That is also how you find redundant agents. questions.csv only shows survivors,
so it can never tell you that an agent has proposed forty questions and won
nothing. This file and proposals.csv can.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import config


class RunLog:
    def __init__(self, today: dt.date):
        self.today = today
        self.lines: list[str] = []
        self.path: Path = config.LOGS / f"{today.isoformat()}.md"

    # -- writing -------------------------------------------------------------

    def heading(self, text: str) -> None:
        self.lines.append("")
        self.lines.append(f"## {text}")
        self.lines.append("")
        print(f"\n=== {text}")

    def sub(self, text: str) -> None:
        self.lines.append("")
        self.lines.append(f"### {text}")
        self.lines.append("")
        print(f"\n-- {text}")

    def info(self, text: str) -> None:
        self.lines.append(text)
        print(text)

    def warn(self, text: str) -> None:
        self.lines.append(f"**WARNING:** {text}")
        print(f"WARNING: {text}")

    def block(self, text: str) -> None:
        self.lines.append("")
        self.lines.append("```")
        self.lines.append(text)
        self.lines.append("```")
        self.lines.append("")

    def flag(self, text: str) -> None:
        """Something you should actually look at."""
        self.lines.append("")
        self.lines.append(f"> **FLAGGED FOR YOUR ATTENTION:** {text}")
        self.lines.append("")
        print(f"\n*** FLAGGED: {text}\n")

    # -- finishing -----------------------------------------------------------

    def save(self) -> None:
        config.LOGS.mkdir(parents=True, exist_ok=True)
        header = [
            f"# Run log -- {self.today.isoformat()}",
            "",
            f"_Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
            "",
        ]
        # Append if the file already exists, so two runs on the same day are
        # both preserved rather than one silently overwriting the other.
        mode = "a" if self.path.exists() else "w"
        with open(self.path, mode, encoding="utf-8") as fh:
            if mode == "a":
                fh.write("\n\n---\n\n")
                fh.write(f"_Second run, {dt.datetime.now().strftime('%H:%M:%S')}_\n")
            else:
                fh.write("\n".join(header))
            fh.write("\n".join(self.lines))
            fh.write("\n")
        print(f"\nLog written to {self.path}")
