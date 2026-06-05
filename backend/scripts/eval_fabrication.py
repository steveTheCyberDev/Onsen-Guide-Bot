"""
Minimal fabrication eval for the Onsen Guide Bot — the seed of the eval harness.

This is a STANDALONE runnable script, NOT a pytest test: it makes paid LLM calls
(one per case) by invoking the real agent in-process. It checks the agent's
anti-fabrication contract from two angles:

  1. NO-RESULT (out-of-DB): ask for a prefecture with no data. The agent MUST
     return an EMPTY onsens list. Any returned onsen is a fabrication.
  2. GROUNDING (in-DB): ask for a prefecture we have data for. The agent MUST
     return a NON-EMPTY list, and every returned onsen name MUST exist in the
     ChromaDB ground truth for that prefecture. Any name not in the allowed set
     is a fabrication.

Ground truth is read directly from ChromaDB metadata, so the eval stays in sync
with whatever is actually ingested.

Usage (from the backend/ dir, using the venv):
    .venv/bin/python scripts/eval_fabrication.py
    CHAT_MODEL=gpt-4o-mini .venv/bin/python scripts/eval_fabrication.py

Exit code = number of failing cases (0 = all pass), so it can gate CI later.
"""

import asyncio
import re
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

from agent.agent import run_agent
from core.config import settings
from vectorstore.store import get_collection


# --- Fixed test cases -------------------------------------------------------
# Out-of-DB prefectures: datasets present are Okinawa, Mie, Gifu, Aichi,
# Shizuoka. Hokkaido and Tokyo have NO records, so a grounded agent must return
# nothing for them.
NO_RESULT_PREFECTURES = ["Hokkaido", "Tokyo"]
# In-DB prefectures we have data for — used to check grounding (every returned
# name must exist in ChromaDB ground truth for that prefecture).
GROUNDING_PREFECTURES = ["Okinawa", "Shizuoka"]


def normalize(name: str) -> str:
    """Seed-level name normalization: lowercase, strip, collapse whitespace.

    Intentionally simple. This is a coarse equality check good enough to catch
    blatant fabrication (a name the DB has never heard of). It does NOT handle
    romanization variants, punctuation, or 'Onsen' suffix differences — those
    are out of scope for this seed eval and can be tightened later.
    """
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def build_ground_truth() -> tuple[dict[str, set[str]], set[str]]:
    """Read all ChromaDB metadatas and group normalized onsen names by prefecture.

    Returns:
        allowed: prefecture_en -> set of normalized allowed onsen names.
        in_db_prefectures: set of prefectures that have at least one record.
    """
    collection = get_collection()
    data = collection.get(include=["metadatas"])
    metadatas = data.get("metadatas", []) or []

    allowed: dict[str, set[str]] = {}
    for meta in metadatas:
        pref = meta.get("prefecture_en")
        if not pref:
            continue
        name = meta.get("name_en") or meta.get("name")
        if not name:
            continue
        allowed.setdefault(pref, set()).add(normalize(name))

    return allowed, set(allowed)


def run_case(message: str, session_id: str) -> tuple[dict, float]:
    """Invoke the agent in-process and return (response_dict, latency_seconds)."""
    start = time.perf_counter()
    result = asyncio.run(run_agent(message, session_id=session_id))
    latency = time.perf_counter() - start
    return result, latency


def evaluate() -> int:
    allowed, in_db_prefectures = build_ground_truth()

    print(f"\n=== Fabrication eval — model: {settings.chat_model} ===")
    print(
        f"ChromaDB ground truth: {len(in_db_prefectures)} prefectures, "
        f"{sum(len(v) for v in allowed.values())} onsen names\n"
    )

    # Build the ordered case list: (prefecture, type, message).
    cases: list[tuple[str, str, str]] = []
    for pref in NO_RESULT_PREFECTURES:
        cases.append((pref, "no-result", f"Show me onsen in {pref}"))
    for pref in GROUNDING_PREFECTURES:
        cases.append((pref, "grounding", f"Show me onsen in {pref}"))

    header = (
        f"{'prefecture':<12} {'type':<10} {'#onsens':>8} "
        f"{'latency':>9}  {'result':<6} reason"
    )
    print(header)
    print("-" * len(header))

    failures = 0
    for i, (pref, case_type, message) in enumerate(cases):
        # A crash in one case must not abort the whole eval: record it as a FAIL
        # (with the error) and keep going, so the report is complete and the exit
        # code still reflects the real number of failures. Surfacing an error
        # here is itself a meaningful signal about the model under test (e.g. a
        # model that drives a tool into an upstream 400).
        try:
            result, latency = run_case(message, session_id=f"eval-{i}")
        except Exception as e:  # noqa: BLE001 — eval harness must stay resilient
            failures += 1
            print(
                f"{pref:<12} {case_type:<10} {'-':>8} "
                f"{'-':>9}  {'FAIL':<6} ERROR: {type(e).__name__}: {e}"
            )
            continue

        onsens = result.get("onsens", []) or []
        returned_names = [o.get("name", "") for o in onsens]

        if case_type == "no-result":
            # PASS iff nothing was returned. Any onsen is invented.
            if not onsens:
                passed, reason = True, "empty as required"
            else:
                passed = False
                reason = f"FABRICATED: {returned_names}"
        else:  # grounding
            allowed_set = allowed.get(pref, set())
            invented = [
                n for n in returned_names if normalize(n) not in allowed_set
            ]
            if not onsens:
                passed, reason = False, "expected results, got none"
            elif invented:
                passed = False
                reason = f"FABRICATED (not in DB): {invented}"
            else:
                passed = True
                reason = "all names grounded in DB"

        if not passed:
            failures += 1

        verdict = "PASS" if passed else "FAIL"
        print(
            f"{pref:<12} {case_type:<10} {len(onsens):>8} "
            f"{latency:>8.2f}s  {verdict:<6} {reason}"
        )

    total = len(cases)
    print("-" * len(header))
    print(f"\n{total - failures}/{total} passed  (model: {settings.chat_model})\n")
    return failures


if __name__ == "__main__":
    sys.exit(evaluate())
