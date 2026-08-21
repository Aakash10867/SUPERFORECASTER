"""Loads configuration files and defines where everything lives on disk."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

# The project root is the directory containing this package's parent.
ROOT = Path(__file__).resolve().parent.parent

INBOX = ROOT / "inbox"
CONFIG = ROOT / "config"
DATA = ROOT / "data"
LOGS = ROOT / "logs"

# -- stage one data files ---------------------------------------------------
QUESTIONS_CSV = DATA / "questions.csv"
PROPOSALS_CSV = DATA / "proposals.csv"
FORECASTS_CSV = DATA / "forecasts.csv"
PROCESSED_CSV = DATA / "processed.csv"
WAITING_CSV = DATA / "waiting_list.csv"
PENDING_TAGS_CSV = DATA / "pending_tags.csv"

# -- stage two (forecasting) data files -------------------------------------
SCREENS_CSV = DATA / "screens.csv"
LENS_CSV = DATA / "lens_outputs.csv"
DIAGNOSTICS_CSV = DATA / "diagnostics.csv"
SYSTEM_PROPOSALS_CSV = DATA / "system_proposals.csv"

RUNS = DATA / "runs"            # runs/YYYY-MM-DD/QXXXX.json -- full reasoning
REFERENCE = DATA / "reference"  # the reference-class library
REPORTS = DATA / "reports"      # calibration, divergence, redundancy

REFERENCE_INDEX_CSV = REFERENCE / "index.csv"
QUOTA_JSON = DATA / "quota.json"

# -- config files -----------------------------------------------------------
SETTINGS_YAML = CONFIG / "settings.yaml"
MODELS_YAML = CONFIG / "models.yaml"
AGENTS_YAML = CONFIG / "agents.yaml"
LENSES_YAML = CONFIG / "lenses.yaml"
LEXICON_CSV = CONFIG / "lexicon.csv"
OVERRIDES_CSV = CONFIG / "overrides.csv"
RESOLUTIONS_CSV = CONFIG / "resolutions.csv"

# Two keys, from two DIFFERENT Google Cloud projects, so their free-tier
# quotas are genuinely separate rather than shared. The router rotates between
# them and tracks each one's usage independently. The second is optional.
API_KEY_ENVS = ["SUPERFORECASTER_API", "SUPERFORECASTER_API2"]


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings() -> dict:
    return _load_yaml(SETTINGS_YAML)


def load_models() -> dict:
    return _load_yaml(MODELS_YAML)


def load_agents() -> dict:
    return _load_yaml(AGENTS_YAML)


def load_lenses() -> dict:
    return _load_yaml(LENSES_YAML)


def api_keys() -> list[tuple[str, str]]:
    """
    Return [(env_name, key), ...] for every key that is actually set.

    Only the first is required. Without the second the system still runs; it
    just has half the daily capacity.
    """
    found = []
    for env in API_KEY_ENVS:
        key = os.environ.get(env, "").strip()
        if key:
            found.append((env, key))
    if not found:
        raise RuntimeError(
            f"No API key found. Set the environment variable {API_KEY_ENVS[0]}.\n"
            "In GitHub Actions this comes from a repository secret of the same name.\n"
            f"An optional second key in {API_KEY_ENVS[1]} doubles the daily quota, "
            "but only if it belongs to a DIFFERENT Google Cloud project."
        )
    return found


def api_key() -> str:
    """First key only. Kept so older code paths keep working."""
    return api_keys()[0][1]


def ensure_dirs() -> None:
    for d in (INBOX, DATA, LOGS, RUNS, REFERENCE, REPORTS):
        d.mkdir(parents=True, exist_ok=True)
