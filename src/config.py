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

# Data files
QUESTIONS_CSV = DATA / "questions.csv"
PROPOSALS_CSV = DATA / "proposals.csv"
FORECASTS_CSV = DATA / "forecasts.csv"
PROCESSED_CSV = DATA / "processed.csv"
WAITING_CSV = DATA / "waiting_list.csv"
PENDING_TAGS_CSV = DATA / "pending_tags.csv"

# Config files
SETTINGS_YAML = CONFIG / "settings.yaml"
MODELS_YAML = CONFIG / "models.yaml"
AGENTS_YAML = CONFIG / "agents.yaml"
LEXICON_CSV = CONFIG / "lexicon.csv"
OVERRIDES_CSV = CONFIG / "overrides.csv"

API_KEY_ENV = "SUPERFORECASTER_API"


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


def api_key() -> str:
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise RuntimeError(
            f"No API key found. Set the environment variable {API_KEY_ENV}.\n"
            "In GitHub Actions this comes from a repository secret of the same name."
        )
    return key


def ensure_dirs() -> None:
    for d in (INBOX, DATA, LOGS):
        d.mkdir(parents=True, exist_ok=True)
