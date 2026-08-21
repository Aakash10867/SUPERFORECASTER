#!/usr/bin/env python3
"""
Ask the API which models your keys can actually use.

Model names change, and a wrong name is not fatal -- the system falls through
to the next model in the chain -- but you will be silently wasting the models
you meant to use. Run this and correct config/models.yaml.

It now checks BOTH keys, because they are supposed to come from two different
Google Cloud projects. If both keys show identical model lists that is normal;
what matters is whether their QUOTAS are separate, which this cannot see from
outside. The run log will tell you: if data/quota.json shows one key hitting
its daily limit while the other keeps working, they are genuinely separate.

    python verify_models.py
"""

import os
import sys

import requests
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src import config  # noqa: E402


def _models_for(key: str):
    r = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": key},
        params={"pageSize": 200},
        timeout=60,
    )
    if r.status_code != 200:
        return None, f"API returned {r.status_code}: {r.text[:300]}"
    available = []
    for m in r.json().get("models", []):
        name = m.get("name", "").replace("models/", "")
        methods = m.get("supportedGenerationMethods", []) or []
        if "generateContent" in methods or "embedContent" in methods:
            available.append(name)
    return set(available), None


def _check_grounding(key: str, model: str):
    """
    Does search grounding ACTUALLY fire on this model with this key?

    A quota row in the dashboard is not proof. Worse, the API happily answers
    an ungrounded question when you ask for search -- you get a normal-looking
    reply with no groundingMetadata. That would give us a reference-class entry
    marked "verified" that was never verified, so we test it rather than assume.
    """
    body = {
        "contents": [{"parts": [{"text":
            "In which years did the US Federal Reserve begin a rate-hiking "
            "cycle? Answer briefly."}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 512},
    }
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=body, timeout=90,
        )
    except requests.RequestException as exc:
        return False, f"network error: {type(exc).__name__}"
    if r.status_code != 200:
        return False, f"http {r.status_code}"
    try:
        cand = r.json()["candidates"][0]
    except (KeyError, IndexError, ValueError):
        return False, "malformed response"
    meta = cand.get("groundingMetadata") or {}
    if not meta:
        return False, "answered, but WITHOUT grounding"
    n = len(meta.get("groundingChunks") or [])
    return True, f"grounding fired, {n} source(s)"


def main() -> int:
    try:
        keys = config.api_keys()
    except RuntimeError as exc:
        print(exc)
        return 1

    print(f"Keys found: {', '.join(name for name, _ in keys)}")
    if len(keys) == 1:
        print(
            f"Only one key. Setting {config.API_KEY_ENVS[1]} to a key from a "
            "DIFFERENT Google Cloud project doubles the daily quota; a second "
            "key in the same project shares one pool and adds nothing.\n"
        )

    cfg = yaml.safe_load(open(config.MODELS_YAML, encoding="utf-8"))
    configured = set()
    for chain in (cfg.get("chains") or {}).values():
        configured.update(chain)

    all_ok = True
    per_key = {}
    for name, key in keys:
        available, err = _models_for(key)
        if err:
            print(f"\n{name}: {err}")
            all_ok = False
            continue
        per_key[name] = available
        print(f"\n{name}: {len(available)} usable models")
        missing = sorted(configured - available)
        if missing:
            all_ok = False
            print("  " + "=" * 66)
            print("  MODELS IN config/models.yaml THIS KEY CANNOT SEE.")
            print("  They will be skipped at runtime. Correct the names.")
            for m in missing:
                print(f"    {m}")
            print("  " + "=" * 66)
        else:
            print("  every configured model is available")

    if len(per_key) == 2:
        a, b = list(per_key.values())
        only_a = sorted(a - b)
        only_b = sorted(b - a)
        if only_a or only_b:
            print("\nThe two keys do NOT see the same models:")
            for m in only_a:
                print(f"  only {list(per_key)[0]}: {m}")
            for m in only_b:
                print(f"  only {list(per_key)[1]}: {m}")

    # -- grounding ---------------------------------------------------------
    grounding_models = cfg.get("grounding_models") or []
    if grounding_models:
        print("\nSearch grounding (used only to verify reference-class cases):")
        name, key = keys[0]
        any_worked = False
        for model in grounding_models:
            if per_key.get(name) and model not in per_key[name]:
                print(f"  {model}: not available to this key")
                continue
            ok, detail = _check_grounding(key, model)
            print(f"  {model}: {detail}")
            any_worked = any_worked or ok
        if not any_worked:
            print(
                "  No model grounded successfully. That is not fatal -- "
                "reference entries will simply be stored as 'unverified' -- "
                "but you can set reference.verify_with_grounding to false in\n"
                "  config/settings.yaml to stop trying."
            )

    print("\nAll model names check out." if all_ok
          else "\nSome names need correcting -- see above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
