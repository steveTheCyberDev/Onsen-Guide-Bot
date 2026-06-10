"""LangSmith experiment harness for the V2/V2.5 onsen workflow.

This is a STANDALONE runnable script, NOT a pytest test: it makes PAID LLM calls
(one full ``run_workflow`` flow per dataset example, each spanning the intent
parse plus, for recommend examples, the analyze brain) and uploads per-example
scores, cost, latency, and traces to LangSmith via ``langsmith.evaluate()``.

What it gives you over the seed ``eval_fabrication.py``:
  * a versioned LangSmith DATASET (``onsen-flow-evals``) covering all 3 modes
    (search / recommend / ask) plus no-data edge cases,
  * 4 EVALUATORS scoring grounding, structural correctness per mode, cost
    budget, and latency,
  * results land in LangSmith as an EXPERIMENT, so runs are comparable
    run-over-run and across models, with cost/latency captured per example.

Ground truth for grounding is read from ChromaDB metadata at runtime (per
prefecture), so the eval stays in sync with whatever is actually ingested —
the same pattern as ``eval_fabrication.py``. No onsen names are hardcoded.

Requirements:
  * ``LANGSMITH_API_KEY`` set (in backend/.env), and the APAC endpoint
    ``https://apac.api.smith.langchain.com`` — the SDK 403s on the US default
    if your workspace is APAC.
  * paid OpenAI access (one flow run per example).

Usage (from the backend/ dir, using the venv):
    .venv/bin/python scripts/eval_flow.py

Experiments are sent to a DEDICATED project (env ``LANGSMITH_EVAL_PROJECT``,
default ``onsen-guide-bot-evals``) so eval runs do not pollute prod traffic.

Exit code = number of failing (example, evaluator) pairs (0 = all pass), so it
can gate CI later.
"""

import asyncio
import os
import re
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv

load_dotenv(BACKEND_DIR / ".env")

# --- Eval project (keep eval runs out of the prod/dev tracing project) --------
# The flow itself traces to settings.langsmith_project; experiments and their
# child runs should land in a SEPARATE project so eval traffic never mixes with
# real /chat traffic. Override via LANGSMITH_EVAL_PROJECT.
EVAL_PROJECT = os.getenv("LANGSMITH_EVAL_PROJECT", "onsen-guide-bot-evals")

# IMPORT-ORDER CRITICAL — do NOT move this below the project-code imports.
# core.config.export_langsmith_env() runs at agent-import time and uses
# os.environ.setdefault(...) for LANGSMITH_PROJECT; langsmith also caches env
# vars (lru_cache). So the eval project must be in os.environ BEFORE the first
# `vectorstore.*` / `agent.*` / `langsmith` import fires — otherwise the flow's
# CHILD workflow traces land in the default (prod/dev) project. langsmith honors
# both the LANGSMITH_* and legacy LANGCHAIN_* aliases, so set both.
os.environ["LANGSMITH_PROJECT"] = EVAL_PROJECT
os.environ["LANGCHAIN_PROJECT"] = EVAL_PROJECT

from vectorstore.store import get_collection

DATASET_NAME = "onsen-flow-evals"

# --- Per-mode budgets (constants, with headroom over measured baselines) ------
# Measured baselines (2026-06): search ~ $0.0017 / recommend ~ $0.005 cost;
# latency is LLM-bound. Thresholds are deliberately loose so a normal run passes
# and only a real regression (e.g. a model swap that balloons tokens, or a slow
# upstream) trips the budget. Tune as the workflow and models change.
COST_BUDGET_USD: dict[str, float] = {
    "search": 0.01,
    "recommend": 0.05,
    "ask": 0.01,
    "no-data": 0.01,  # no-data examples are search-mode; cheap.
}
LATENCY_BUDGET_MS: dict[str, int] = {
    "search": 8000,
    "recommend": 20000,
    "ask": 8000,
    "no-data": 8000,
}


# --- Ground truth -------------------------------------------------------------
def normalize(name: str) -> str:
    """Coarse name normalization: lowercase, strip, collapse whitespace.

    Same intentionally-simple equality used by ``eval_fabrication.py`` — good
    enough to catch blatant fabrication (a name the DB never heard of). Does NOT
    handle romanization variants or 'Onsen'-suffix differences; that's a later
    tightening.
    """
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def build_ground_truth() -> dict[str, set[str]]:
    """Read all ChromaDB metadatas and group normalized onsen names by prefecture.

    Returns a mapping ``prefecture_en -> set of normalized allowed names``. A
    prefecture absent from the mapping has NO records (so a grounded flow must
    return nothing for it). Mirrors ``eval_fabrication.py::build_ground_truth``.
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
    return allowed


# --- Dataset examples ---------------------------------------------------------
# Each example: a user message + metadata describing what a correct flow run
# should produce. `expected_mode` drives the structure/cost/latency evaluators;
# `prefecture` + `has_data` drive grounding. `has_data` here is the AUTHORED
# expectation; at runtime it is reconciled against ChromaDB ground truth so a
# prefecture that later gets data won't silently keep failing as "no-data".
_EXAMPLES: list[dict] = [
    {
        "message": "Find onsen in Okinawa",
        "expected_mode": "search",
        "prefecture": "Okinawa",
        "has_data": True,
        "wants_hotels": False,
    },
    {
        "message": "Find onsen in Shizuoka",
        "expected_mode": "search",
        "prefecture": "Shizuoka",
        "has_data": True,
        "wants_hotels": False,
    },
    {
        "message": (
            "Recommend an onsen in Okinawa for a couple wanting a quiet "
            "relaxing soak"
        ),
        "expected_mode": "recommend",
        "prefecture": "Okinawa",
        "has_data": True,
        "wants_hotels": False,
    },
    {
        "message": "Recommend an onsen in Shizuoka and some nearby hotels",
        "expected_mode": "recommend",
        "prefecture": "Shizuoka",
        "has_data": True,
        "wants_hotels": True,
    },
    {
        "message": "What is onsen etiquette if I have tattoos?",
        "expected_mode": "ask",
        "prefecture": None,
        "has_data": False,
        "wants_hotels": False,
    },
    {
        "message": "Find onsen in Hokkaido",
        "expected_mode": "no-data",
        "prefecture": "Hokkaido",
        "has_data": False,
        "wants_hotels": False,
    },
    {
        "message": "Find onsen in Tokyo",
        "expected_mode": "no-data",
        "prefecture": "Tokyo",
        "has_data": False,
        "wants_hotels": False,
    },
]


def reconcile_has_data(examples: list[dict], allowed: dict[str, set[str]]) -> list[dict]:
    """Reconcile each example's authored ``has_data`` against ChromaDB truth.

    For examples that name a prefecture, ``has_data`` becomes whether that
    prefecture currently has any records. This keeps the dataset honest if a
    prefecture later gets ingested (a former "no-data" example would otherwise
    keep asserting emptiness forever). Examples with no prefecture (ask mode)
    keep their authored value. Returns NEW dicts; does not mutate the inputs.
    """
    out: list[dict] = []
    for ex in examples:
        ex = dict(ex)
        pref = ex.get("prefecture")
        if pref is not None:
            ex["has_data"] = pref in allowed
        out.append(ex)
    return out


def get_or_create_dataset(client, allowed: dict[str, set[str]]):
    """Idempotently get-or-create the ``onsen-flow-evals`` dataset.

    Creates the dataset if missing and (only then) populates it with the
    reconciled examples. If it already exists, it is reused as-is so re-runs
    don't duplicate examples; delete it in the LangSmith UI to re-seed.
    """
    if client.has_dataset(dataset_name=DATASET_NAME):
        return client.read_dataset(dataset_name=DATASET_NAME)

    dataset = client.create_dataset(
        dataset_name=DATASET_NAME,
        description=(
            "Onsen Guide Bot V2/V2.5 flow evals — all 3 modes (search/recommend/"
            "ask) + no-data edge cases. Inputs are user messages; each example's "
            "reference outputs carry the expectations "
            "(expected_mode / prefecture / has_data / wants_hotels)."
        ),
    )
    examples = reconcile_has_data(_EXAMPLES, allowed)
    # Expectations go in the example's reference OUTPUTS (not metadata): the
    # LangSmith 0.8.x evaluator arg-binding injects `reference_outputs` by name
    # but NOT `metadata`, so evaluators read expectations from reference_outputs.
    # They are ALSO duplicated into metadata purely for at-a-glance UI context.
    expectations = [
        {
            "expected_mode": ex["expected_mode"],
            "prefecture": ex["prefecture"],
            "has_data": ex["has_data"],
            "wants_hotels": ex["wants_hotels"],
        }
        for ex in examples
    ]
    client.create_examples(
        dataset_id=dataset.id,
        inputs=[{"message": ex["message"]} for ex in examples],
        outputs=expectations,
        metadata=expectations,
    )
    return dataset


# --- Target -------------------------------------------------------------------
def make_target_with_usage():
    """Target variant that injects our usage callback into ``run_workflow``.

    ``run_workflow`` builds its own ``UsageMetadataCallbackHandler`` and does not
    accept an external one. To capture cost for the eval without editing the
    pipeline signature, we monkeypatch ``UsageMetadataCallbackHandler`` in the
    pipeline module so the instance it creates is one we can read afterwards.
    This is eval-only glue and is reverted per call.

    NOTE: this factory does NOT toggle ``settings.analyze_enabled``. The eval
    needs analyze mode ON (so recommend examples exercise the analyze brain), but
    that global is flipped — and RESTORED — around the ``evaluate()`` run in
    ``run_evaluation()`` so importing/calling this module from a long-lived
    process (CI/pytest) never permanently mutates the prod setting.
    """
    from agent.workflow import pipeline
    from agent.workflow.cost import summarize_usage

    _counter = {"n": 0}

    def target(inputs: dict) -> dict:
        message = inputs["message"]
        _counter["n"] += 1
        session_id = f"eval-flow-{_counter['n']}-{int(time.time())}"

        captured = {}

        real_cls = pipeline.UsageMetadataCallbackHandler

        def _factory(*args, **kwargs):
            cb = real_cls(*args, **kwargs)
            captured["cb"] = cb
            return cb

        pipeline.UsageMetadataCallbackHandler = _factory  # type: ignore[assignment]
        started = time.monotonic()
        try:
            result = asyncio.run(pipeline.run_workflow(message, session_id=session_id))
        finally:
            pipeline.UsageMetadataCallbackHandler = real_cls  # type: ignore[assignment]
        latency_ms = int((time.monotonic() - started) * 1000)

        cb = captured.get("cb")
        usage_meta = getattr(cb, "usage_metadata", {}) if cb else {}
        summary = summarize_usage(usage_meta)

        return {
            **result,
            "_cost_usd": summary["cost_usd"],
            "_latency_ms": latency_ms,
        }

    return target


# --- Evaluators ---------------------------------------------------------------
# LangSmith (0.8.x) injects evaluator args BY NAME from a fixed supported set:
# run, example, inputs, outputs, reference_outputs, attachments. `metadata` is
# NOT in that set, so the per-example EXPECTATIONS are stored in the example's
# reference OUTPUTS and read here as `reference_outputs` — `outputs` is the
# target's returned AgentResponse (+ _cost_usd / _latency_ms). Each evaluator
# returns a dict like {"key": ..., "score": 0/1}.
#
# Ground truth is captured once (module-level) so evaluators are pure functions
# of (outputs, reference_outputs) given that snapshot. set_ground_truth() injects it.

_GROUND_TRUTH: dict[str, set[str]] = {}


def set_ground_truth(allowed: dict[str, set[str]]) -> None:
    """Inject the ChromaDB ground-truth snapshot the grounding evaluator reads."""
    global _GROUND_TRUTH
    _GROUND_TRUTH = allowed


def _onsen_names(outputs: dict) -> list[str]:
    return [o.get("name", "") for o in (outputs.get("onsens") or [])]


def grounding(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff every returned onsen name is in the prefecture's ground truth.

    For ``has_data=False`` examples, onsens MUST be empty (any onsen is invented).
    For ``has_data=True``, every returned name must be in the ChromaDB allowed set
    for the example's prefecture.

    TODO(LLM-judge): pros/cons fabrication is fuzzy (free text derived from the
    description) and is NOT checked here — only name-level grounding is. Add an
    LLM-judge evaluator for pros/cons groundedness later.
    """
    has_data = bool(reference_outputs.get("has_data"))
    names = _onsen_names(outputs)

    if not has_data:
        score = 1 if not names else 0
        return {"key": "grounding", "score": score}

    pref = reference_outputs.get("prefecture")
    allowed = _GROUND_TRUTH.get(pref, set())
    if not names:
        # Expected results but got none — not a grounding failure per se, but the
        # flow returned nothing where data exists. Treat as ungrounded=fail so it
        # surfaces; the structure evaluator also covers emptiness.
        return {"key": "grounding", "score": 0}
    invented = [n for n in names if normalize(n) not in allowed]
    return {"key": "grounding", "score": 0 if invented else 1}


def structure(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff the response shape matches the expected mode.

    recommend ⇒ recommendation non-null AND ≥1 onsen has non-empty pros.
    search    ⇒ recommendation is None AND every onsen has empty pros & cons.
    ask       ⇒ onsens empty AND reply is the ask stub.
    no-data   ⇒ onsens empty.
    """
    mode = reference_outputs.get("expected_mode")
    onsens = outputs.get("onsens") or []
    recommendation = outputs.get("recommendation")
    reply = outputs.get("reply") or ""

    if mode == "recommend":
        has_pros = any((o.get("pros") or []) for o in onsens)
        ok = recommendation is not None and has_pros
    elif mode == "search":
        no_proscons = all(
            not (o.get("pros") or []) and not (o.get("cons") or []) for o in onsens
        )
        ok = recommendation is None and no_proscons
    elif mode == "ask":
        ok = not onsens and reply == _ask_stub_reply()
    elif mode == "no-data":
        ok = not onsens
    else:
        ok = False

    return {"key": "structure", "score": 1 if ok else 0}


def _ask_stub_reply() -> str:
    """The ask-mode stub reply, read from the pipeline so the two never drift."""
    from agent.workflow import pipeline

    return pipeline._ASK_STUB_REPLY


def cost_budget(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff the run's measured cost is within the per-mode budget."""
    mode = reference_outputs.get("expected_mode")
    budget = COST_BUDGET_USD.get(mode, COST_BUDGET_USD["search"])
    cost = float(outputs.get("_cost_usd", 0.0) or 0.0)
    return {"key": "cost_budget", "score": 1 if cost <= budget else 0}


def latency(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff the run's measured latency is within the per-mode budget."""
    mode = reference_outputs.get("expected_mode")
    budget = LATENCY_BUDGET_MS.get(mode, LATENCY_BUDGET_MS["search"])
    measured = int(outputs.get("_latency_ms", 0) or 0)
    return {"key": "latency", "score": 1 if measured <= budget else 0}


EVALUATORS = [grounding, structure, cost_budget, latency]


# --- Runner -------------------------------------------------------------------
def run_evaluation() -> int:
    """Create-or-get the dataset, run the experiment, print a report.

    Returns the number of failing (example, evaluator) pairs (the exit code).
    """
    from langsmith import Client, evaluate

    if not os.getenv("LANGSMITH_API_KEY"):
        print(
            "LANGSMITH_API_KEY is not set — cannot run the live eval. Set it (and "
            "the APAC endpoint https://apac.api.smith.langchain.com) in backend/.env.",
            file=sys.stderr,
        )
        return 1

    # NOTE: LANGSMITH_PROJECT / LANGCHAIN_PROJECT routing for the flow's child
    # traces is set at MODULE-TOP (see the import-order comment there), not here —
    # by the time this function runs the agent module is already imported and
    # langsmith has cached the project, so setting it here would be too late for
    # child traces. The experiment itself is grouped by experiment_prefix below.
    client = Client()

    allowed = build_ground_truth()
    set_ground_truth(allowed)
    print(
        f"ChromaDB ground truth: {len(allowed)} prefectures, "
        f"{sum(len(v) for v in allowed.values())} onsen names"
    )
    print(f"Dataset: {DATASET_NAME} | Experiment project: {EVAL_PROJECT}\n")

    get_or_create_dataset(client, allowed)

    target = make_target_with_usage()

    # The eval needs analyze mode ON so recommend examples exercise the analyze
    # brain. Flip it for the duration of evaluate() and RESTORE the prior value
    # in finally — so calling run_evaluation() from a long-lived process
    # (CI/pytest) never permanently flips the prod setting, even if evaluate()
    # raises. (settings is a module-level singleton; importing here keeps the
    # config import lazy, consistent with the rest of this module.)
    from core.config import settings

    prior_analyze_enabled = settings.analyze_enabled
    settings.analyze_enabled = True
    try:
        # Tag the experiment with the analyze model so two runs that differ only
        # by ANALYZE_MODEL (the model-comparison use case) are distinguishable in
        # the LangSmith UI — both in the experiment name and in its metadata.
        results = evaluate(
            target,
            data=DATASET_NAME,
            evaluators=EVALUATORS,
            experiment_prefix=f"onsen-flow-analyze-{settings.analyze_model}",
            metadata={
                "harness": "eval_flow.py",
                "analyze_model": settings.analyze_model,
                "intent_model": settings.intent_model,
            },
            # Send the experiment + its child runs to the dedicated eval project.
            client=client,
            max_concurrency=1,  # serialize to keep latency measurements clean.
        )
    finally:
        settings.analyze_enabled = prior_analyze_enabled

    return _report(results)


def _report(results) -> int:
    """Print a per-example PASS/FAIL table + per-evaluator pass rates.

    Returns the count of failing (example, evaluator) pairs.
    """
    eval_keys = [e.__name__ for e in EVALUATORS]
    # Map evaluator function name → the "key" it emits (they match here).
    rows: list[tuple[str, str, dict[str, int]]] = []
    per_eval_pass: dict[str, int] = {k: 0 for k in eval_keys}
    per_eval_total: dict[str, int] = {k: 0 for k in eval_keys}
    failures = 0

    for res in results:
        example = res["example"]
        run = res["run"]
        meta = (example.metadata or {})
        mode = meta.get("expected_mode", "?")
        message = (example.inputs or {}).get("message", "")

        scores: dict[str, int] = {}
        for er in res["evaluation_results"]["results"]:
            key = er.key
            score = int(er.score) if er.score is not None else 0
            scores[key] = score
            if key in per_eval_total:
                per_eval_total[key] += 1
                per_eval_pass[key] += score
            if score == 0:
                failures += 1

        rows.append((mode, message, scores))

    # Print table.
    print("\n=== onsen-flow experiment results ===\n")
    header = f"{'mode':<10} {'grounding':>9} {'structure':>9} {'cost':>6} {'latency':>8}  message"
    print(header)
    print("-" * len(header))
    for mode, message, scores in rows:
        def cell(k: str) -> str:
            v = scores.get(k)
            return "PASS" if v == 1 else ("FAIL" if v == 0 else "-")

        print(
            f"{mode:<10} {cell('grounding'):>9} {cell('structure'):>9} "
            f"{cell('cost_budget'):>6} {cell('latency'):>8}  {message[:50]}"
        )
    print("-" * len(header))

    print("\nPer-evaluator pass rate:")
    for k in ["grounding", "structure", "cost_budget", "latency"]:
        total = per_eval_total.get(k, 0)
        passed = per_eval_pass.get(k, 0)
        rate = f"{passed}/{total}" if total else "0/0"
        print(f"  {k:<14} {rate}")

    print(f"\nFailing (example, evaluator) pairs: {failures}")
    try:
        url = results.experiment_name  # type: ignore[attr-defined]
        print(f"Experiment: {url}")
    except Exception:
        pass
    return failures


if __name__ == "__main__":
    sys.exit(run_evaluation())
