"""
Persistent daily quota tracking.

THE BUG THIS FIXES
------------------
The original router created a fresh CallStats on every run and then checked
usage against `rpd` -- a limit that resets DAILY, not per run. Run the action
twice in one day and the router believed it had full quota both times, while
Google's counter kept climbing. It survived stage one because generation is
light and you ran once a day. Forecasting roughly triples the load and you run
whenever papers arrive, so it had to be fixed before stage two went live.

Counts are stored per (date, key, model). Two keys from two different projects
have genuinely separate quotas, so they must be counted separately -- pooling
them would waste half the capacity.

Old dates are pruned on write, so the file cannot grow forever. A short history
is kept because it is useful in the log ("yesterday you used 340 calls").
"""

from __future__ import annotations

import datetime as dt
import json

from . import config

KEEP_DAYS = 14


def _blank() -> dict:
    return {"days": {}}


def load() -> dict:
    if not config.QUOTA_JSON.exists():
        return _blank()
    try:
        with open(config.QUOTA_JSON, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "days" not in data:
            return _blank()
        return data
    except (json.JSONDecodeError, OSError):
        # A corrupt quota file must never stop a run. Losing today's counts is
        # far less bad than refusing to start; worst case we over-call once and
        # the API's own 429s catch it.
        return _blank()


def save(data: dict) -> None:
    cutoff = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    data["days"] = {d: v for d, v in data.get("days", {}).items() if d >= cutoff}
    config.DATA.mkdir(parents=True, exist_ok=True)
    tmp = config.QUOTA_JSON.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    tmp.replace(config.QUOTA_JSON)


class Quota:
    """
    Live view of today's usage, backed by the JSON file.

    NOTE ON `today`: this deliberately uses the REAL calendar date, not the
    pipeline's --date override. Google's quota resets on real days. If you
    backfill an old paper with --date, the calls still come out of today's
    allowance.
    """

    def __init__(self, key_names: list[str]):
        self.today = dt.date.today().isoformat()
        self.key_names = key_names
        self._data = load()
        self._day = self._data.setdefault("days", {}).setdefault(self.today, {})
        for name in key_names:
            self._day.setdefault(name, {})

    # -- reading -------------------------------------------------------------

    def used(self, key_name: str, model: str) -> int:
        return int(self._day.get(key_name, {}).get(model, 0))

    def remaining(self, key_name: str, model: str, rpd: int) -> int:
        return max(0, rpd - self.used(key_name, model))

    def total_used(self) -> int:
        return sum(
            n for per_key in self._day.values() for n in per_key.values()
        )

    def summary(self) -> str:
        lines = []
        for key_name in self.key_names:
            per_model = self._day.get(key_name, {})
            if not per_model:
                lines.append(f"  {key_name}: unused today")
                continue
            bits = ", ".join(
                f"{m}={n}" for m, n in sorted(per_model.items(), key=lambda kv: -kv[1])
            )
            lines.append(f"  {key_name}: {bits}")
        return "\n".join(lines) if lines else "  (no usage recorded)"

    # -- writing -------------------------------------------------------------

    def record(self, key_name: str, model: str, n: int = 1) -> None:
        bucket = self._day.setdefault(key_name, {})
        bucket[model] = int(bucket.get(model, 0)) + n

    def flush(self) -> None:
        """
        Write counts to disk.

        Called after every single call rather than at the end of the run. If
        the run crashes halfway through, the calls it already made must still
        be counted -- otherwise a crash loop could burn a whole day's quota
        while the file still says zero.
        """
        save(self._data)
