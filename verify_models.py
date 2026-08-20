#!/usr/bin/env python3
"""
Ask the API which models your key can actually use.

Model names change, and the ones in config/models.yaml are a best guess for the
newer Gemini releases. Run this once and correct the file. A wrong name is not
fatal -- the system falls through to the next model in the chain -- but you
will be wasting the models you meant to use.

    python verify_models.py
"""

import os
import sys

import requests
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src import config  # noqa: E402


def main() -> int:
    try:
        key = config.api_key()
    except RuntimeError as exc:
        print(exc)
        return 1

    r = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": key},
        params={"pageSize": 200},
        timeout=60,
    )
    if r.status_code != 200:
        print(f"API returned {r.status_code}: {r.text[:400]}")
        return 1

    available = []
    for m in r.json().get("models", []):
        name = m.get("name", "").replace("models/", "")
        methods = m.get("supportedGenerationMethods", []) or []
        if "generateContent" in methods or "embedContent" in methods:
            available.append(name)

    available_set = set(available)
    print(f"\n{len(available)} usable models visible to your key:\n")
    for name in sorted(available):
        print(f"  {name}")

    cfg = yaml.safe_load(open(config.MODELS_YAML, encoding="utf-8"))
    configured = set()
    for chain in (cfg.get("chains") or {}).values():
        configured.update(chain)

    missing = sorted(configured - available_set)
    if missing:
        print("\n" + "=" * 70)
        print("MODELS IN config/models.yaml THAT YOUR KEY CANNOT SEE:")
        print("These will be skipped at runtime. Correct the names in")
        print("config/models.yaml using the list above.\n")
        for name in missing:
            print(f"  {name}")
        print("=" * 70)
    else:
        print("\nEvery model in config/models.yaml is available. Nothing to fix.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
