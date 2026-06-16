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

# --- LLM-as-judge model (eval-local; deliberately NOT in core/config) ---------
# The two groundedness judges (proscons_grounding / ask_grounding) are an
# eval-time concern only — they never run in the app — so the model knob lives
# here rather than in core/config.settings. Cheap default; override via
# JUDGE_MODEL for a stronger/cheaper judge. Built once at module level (mirrors
# the rest of the harness) and reused across every judged example.
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gpt-4o-mini")

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
        # "top N" phrasing still routes to search (a location listing, not a
        # preference-driven recommend) — verified against parse_intent. Gifu has
        # data, so grounding checks the returned names against the Gifu set.
        "message": "What's the top 5 onsens in Gifu?",
        "expected_mode": "search",
        "prefecture": "Gifu",
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
        # No-data ask: a question the KB cannot answer (no wifi/password content),
        # so the grounded ask path must return the no-info fallback rather than a
        # fabricated answer. `expect_no_info` flags it for the structure evaluator.
        "message": "What's the wifi password at the onsen?",
        "expected_mode": "ask",
        "prefecture": None,
        "has_data": False,
        "wants_hotels": False,
        "expect_no_info": True,
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


def _expectation(ex: dict) -> dict:
    """Build the reference-output expectation dict for one example.

    Expectations go in the example's reference OUTPUTS (not metadata): the
    LangSmith 0.8.x evaluator arg-binding injects ``reference_outputs`` by name
    but NOT ``metadata``, so evaluators read expectations from reference_outputs.
    They are ALSO duplicated into metadata purely for at-a-glance UI context.
    """
    return {
        "expected_mode": ex["expected_mode"],
        "prefecture": ex["prefecture"],
        "has_data": ex["has_data"],
        "wants_hotels": ex["wants_hotels"],
        # Optional flag (ask-mode only): the answer should be the no-info
        # fallback because the KB cannot answer the question. Defaults False.
        "expect_no_info": ex.get("expect_no_info", False),
    }


def get_or_create_dataset(client, allowed: dict[str, set[str]]):
    """Idempotently get-or-create the ``onsen-flow-evals`` dataset, syncing new examples.

    Creates and seeds the dataset if missing. If it already exists, it is reused
    (so existing examples and their experiment history are preserved) and any
    ``_EXAMPLES`` not already present — keyed by message text — are ADDED. This
    lets new evals be appended to ``_EXAMPLES`` and picked up on the next run
    without deleting/re-seeding the dataset (which would orphan past experiments).
    """
    examples = reconcile_has_data(_EXAMPLES, allowed)

    if client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
        existing_msgs = {
            (e.inputs or {}).get("message")
            for e in client.list_examples(dataset_id=dataset.id)
        }
        missing = [ex for ex in examples if ex["message"] not in existing_msgs]
        if missing:
            client.create_examples(
                dataset_id=dataset.id,
                inputs=[{"message": ex["message"]} for ex in missing],
                outputs=[_expectation(ex) for ex in missing],
                metadata=[_expectation(ex) for ex in missing],
            )
            print(
                f"Added {len(missing)} new example(s) to existing dataset: "
                f"{[ex['message'] for ex in missing]}"
            )
        return dataset

    dataset = client.create_dataset(
        dataset_name=DATASET_NAME,
        description=(
            "Onsen Guide Bot V2/V2.5 flow evals — all 3 modes (search/recommend/"
            "ask) + no-data edge cases. Inputs are user messages; each example's "
            "reference outputs carry the expectations "
            "(expected_mode / prefecture / has_data / wants_hotels)."
        ),
    )
    expectations = [_expectation(ex) for ex in examples]
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


# --- LLM-as-judge --------------------------------------------------------------
# Built once at module level like the rest of the harness (intent/analyze/ask all
# construct their ChatOpenAI at import time). Reused across every judged example.
# Kept lazy-imported so the module still imports in environments without the
# OpenAI dep wired up (the unit tests mock _llm_judge, so they never build this).
def _build_judge_llm():
    """Construct the cheap judge ChatOpenAI once, reading the eval-local model knob."""
    from core.config import settings
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=JUDGE_MODEL,
        api_key=settings.openai_api_key,
        temperature=0,  # deterministic-as-possible verdicts.
    )


_JUDGE_LLM = None


def _llm_judge(system: str, user: str) -> int | None:
    """Ask the judge a single yes/no groundedness question; return 1, 0, or None.

    The judge is prompted to reply with a single token GROUNDED / UNGROUNDED; we
    map ``UNGROUNDED``→0 and ``GROUNDED``→1. Fail-SAFE: any error (API failure,
    rate limit/timeout, missing key) OR unrecognised output (neither token)
    returns ``None`` = ABSTAIN, NOT a pass. For a measurement tool this is the
    honest default — a flaky/broken judge surfaces as "no signal" (rendered "-",
    uncounted) rather than masking as a green PASS. Callers treat None as
    "couldn't judge this item" and skip it; the deterministic name-level
    ``grounding`` evaluator remains the hard guard regardless.
    """
    global _JUDGE_LLM
    try:
        if _JUDGE_LLM is None:
            _JUDGE_LLM = _build_judge_llm()
        resp = _JUDGE_LLM.invoke(
            [("system", system), ("human", user)]
        )
        text = (getattr(resp, "content", "") or "").strip().upper()
        if text.startswith("UNGROUNDED"):
            return 0
        if text.startswith("GROUNDED"):
            return 1
        return None  # unrecognised output → abstain, not a false PASS.
    except Exception:  # noqa: BLE001 — fail-safe: abstain (None), never crash the eval.
        return None


def grounding(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff every returned onsen name is in the prefecture's ground truth.

    For ``has_data=False`` examples, onsens MUST be empty (any onsen is invented).
    For ``has_data=True``, every returned name must be in the ChromaDB allowed set
    for the example's prefecture.

    Name-level grounding only. Pros/cons fabrication (fuzzy free text derived from
    the description) is scored separately by the ``proscons_grounding`` LLM-judge.
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
    ask       ⇒ onsens empty AND recommendation None AND reply non-empty. When the
                harness runs with ask_enabled ON (real RAG answer), reply must ALSO
                differ from the stub (the stub means the answer node never ran).
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
        # The ask answer rides in `reply` (empty onsens, no recommendation). When
        # ask_enabled is ON the reply is a real grounded answer (or the no-info
        # fallback), so it must be non-empty AND not the "coming soon" stub — the
        # stub showing through here means the answer node never ran.
        ok = not onsens and outputs.get("recommendation") is None and bool(reply)
        from core.config import settings

        if settings.ask_enabled:
            ok = ok and reply != _ask_stub_reply()
            # A KB-unanswerable question must land on the exact no-info fallback,
            # never a fabricated answer.
            if reference_outputs.get("expect_no_info"):
                ok = ok and reply == _no_info_reply()
    elif mode == "no-data":
        ok = not onsens
    else:
        ok = False

    return {"key": "structure", "score": 1 if ok else 0}


def _ask_stub_reply() -> str:
    """The ask-mode stub reply, read from the pipeline so the two never drift."""
    from agent.workflow import pipeline

    return pipeline._ASK_STUB_REPLY


def _no_info_reply() -> str:
    """The ask-mode no-info fallback, read from the ask node so they never drift."""
    from agent.workflow.ask import NO_INFO_REPLY

    return NO_INFO_REPLY


# --- LLM-judge groundedness evaluators ----------------------------------------
# Prose analogues of the deterministic name-level `grounding` evaluator: they
# score whether GENERATED free text (per-onsen pros/cons, and the ask answer) is
# supported by its source, closing the "measured not asserted" gap. Both make an
# LLM call AT EVAL TIME ONLY (inside the evaluator, NOT the target), so they do
# NOT count against the target's `_cost_usd` budget. Both ABSTAIN (score=None)
# when they don't apply to the example — _report skips None scores.

_PROSCONS_JUDGE_SYSTEM = (
    "You are a strict grounding judge for an onsen (Japanese hot spring) guide. "
    "You are given ONE onsen's factual fields and the pros and cons a system "
    "generated for it. Decide whether EVERY pro and con is supported by — i.e. a "
    "reasonable reading of — those facts alone, inventing no new facts (no claimed "
    "amenities, prices, scenery, or qualities absent from the fields). Reply with "
    "exactly one word: GROUNDED if all pros/cons are supported, otherwise UNGROUNDED."
)

_ASK_JUDGE_SYSTEM = (
    "You are a strict grounding judge for an onsen knowledge-base assistant. You "
    "are given retrieved source passages and an answer the assistant produced. "
    "Decide whether every factual claim in the answer is supported by the "
    "passages, with no invented facts. Reply with exactly one word: GROUNDED if "
    "the answer is fully supported, otherwise UNGROUNDED."
)


def _onsen_facts_block(onsen: dict) -> str:
    """Render the factual-only fields of one onsen for the proscons judge prompt.

    Deliberately excludes pros/cons (those are what's being judged) and coords/URLs
    (no judgement value), matching the fields the analyze brain derived them from.
    """
    return (
        f"Name: {onsen.get('name', '')}\n"
        f"Location: {onsen.get('location', '')}\n"
        f"Spring type: {onsen.get('spring_type', '')}\n"
        f"Description: {onsen.get('spa_quality', '')}"
    )


def proscons_grounding(outputs: dict, reference_outputs: dict) -> dict:
    """Judge whether each onsen's pros/cons are grounded in that onsen's facts.

    Applies to RECOMMEND examples only — detected structurally as "any returned
    onsen carries pros or cons" (search/no-data leave them empty). ABSTAINS
    (score=None) otherwise, so it never penalises modes that have no pros/cons.

    Score 1 iff EVERY onsen with pros/cons is judged grounded; 0 if any onsen's
    pros/cons invent facts not in its name/location/spring_type/description. A
    judge error on an onsen returns None for that onsen and is SKIPPED (not a
    pass, not a fail); if EVERY onsen errored, the example abstains (None).
    """
    onsens = outputs.get("onsens") or []
    judged = [o for o in onsens if (o.get("pros") or o.get("cons"))]
    if not judged:
        return {"key": "proscons_grounding", "score": None, "comment": "n/a"}

    verdicts: list[int | None] = []
    for onsen in judged:
        user = (
            f"{_onsen_facts_block(onsen)}\n\n"
            f"Pros: {onsen.get('pros') or []}\n"
            f"Cons: {onsen.get('cons') or []}"
        )
        verdict = _llm_judge(_PROSCONS_JUDGE_SYSTEM, user)
        if verdict == 0:
            # Any ungrounded onsen fails the example immediately.
            return {
                "key": "proscons_grounding",
                "score": 0,
                "comment": f"ungrounded pros/cons for {onsen.get('name', '?')}",
            }
        verdicts.append(verdict)

    # No explicit 0. If the judge errored on EVERY onsen (all None) there is no
    # signal → abstain rather than report a false PASS.
    if all(v is None for v in verdicts):
        return {
            "key": "proscons_grounding",
            "score": None,
            "comment": "n/a (judge unavailable)",
        }
    return {"key": "proscons_grounding", "score": 1}


def _ask_question(outputs: dict, inputs: dict | None, example) -> str:
    """Recover the ask question to re-retrieve against.

    The AgentResponse `outputs` carries the one-line `reply`, not the original
    question, so we read it from the LangSmith-injected `inputs` (preferred) or
    fall back to the example's inputs. Both are injected by name by LangSmith 0.8.x.
    """
    if inputs and inputs.get("message"):
        return inputs["message"]
    if example is not None:
        return (getattr(example, "inputs", None) or {}).get("message", "")
    return ""


def ask_grounding(
    outputs: dict, reference_outputs: dict, inputs: dict | None = None, example=None
) -> dict:
    """Judge whether the ask answer's claims are supported by the retrieved KB chunks.

    Applies to ASK examples only (expected_mode == "ask") AND only when the reply
    is a REAL answer — i.e. NOT the no-info fallback and NOT the "coming soon"
    stub. Refusing (the fallback) or the gate being off (the stub) is correct
    behaviour, not a grounding question, so those ABSTAIN (score=None).

    Re-retrieves the KB chunks for the question via the retrieval service (the
    same call the ask node makes) and asks the judge whether the answer is
    supported by them. Score 1/0; None when not applicable.
    """
    if reference_outputs.get("expected_mode") != "ask":
        return {"key": "ask_grounding", "score": None, "comment": "n/a"}

    reply = outputs.get("reply") or ""
    # Abstain on the stub (gate off → answer node never ran) and the no-info
    # fallback (a correct refusal, not a grounding claim).
    if not reply or reply == _ask_stub_reply() or reply == _no_info_reply():
        return {"key": "ask_grounding", "score": None, "comment": "n/a"}

    question = _ask_question(outputs, inputs, example)
    if not question:
        return {"key": "ask_grounding", "score": None, "comment": "n/a (no question)"}

    # Lazy import: keeps the heavy retrieval/Chroma deps out of module import.
    from core.config import settings
    from services.retrieval.retrieval_service import (
        query_knowledge_with_diagnostics,
    )

    chunks, _diag = query_knowledge_with_diagnostics(
        question, settings.ask_top_k, settings.ask_max_distance
    )
    if not chunks:
        # No supporting chunks but a real (non-fallback) answer was produced →
        # ungrounded by definition.
        return {
            "key": "ask_grounding",
            "score": 0,
            "comment": "no KB chunks retrieved for a non-fallback answer",
        }

    passages = "\n\n".join(f"- {c.get('text', '')}" for c in chunks)
    user = f"PASSAGES:\n{passages}\n\nANSWER:\n{reply}"
    verdict = _llm_judge(_ASK_JUDGE_SYSTEM, user)
    if verdict is None:
        # Judge errored → abstain (no signal), not a false PASS.
        return {"key": "ask_grounding", "score": None, "comment": "n/a (judge unavailable)"}
    return {"key": "ask_grounding", "score": verdict}


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


EVALUATORS = [
    grounding,
    proscons_grounding,
    ask_grounding,
    structure,
    cost_budget,
    latency,
]

# Report-table display spec per evaluator key: (short column label, width).
# The single source of truth for the _report() table — header, per-row cells, and
# the per-evaluator pass-rate loop are ALL derived from EVALUATORS + this map, so
# adding an evaluator means adding one entry here (not editing three places).
_COLUMN_LABELS: dict[str, tuple[str, int]] = {
    "grounding": ("ground", 6),
    "proscons_grounding": ("pc-gnd", 6),
    "ask_grounding": ("ask-gnd", 7),
    "structure": ("struct", 6),
    "cost_budget": ("cost", 6),
    "latency": ("latency", 7),
}


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
    # ask mode also needs its gate ON so ask examples exercise the real
    # answer_question RAG node (not the stub). Flipped here and RESTORED in the
    # same finally as analyze_enabled, so a long-lived process never leaks either
    # global even if evaluate() raises.
    prior_ask_enabled = settings.ask_enabled
    settings.analyze_enabled = True
    settings.ask_enabled = True
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
        settings.ask_enabled = prior_ask_enabled

    return _report(results)


def _report(results) -> int:
    """Print a per-example PASS/FAIL table + per-evaluator pass rates.

    Returns the count of failing (example, evaluator) pairs.
    """
    eval_keys = [e.__name__ for e in EVALUATORS]
    # Map evaluator function name → the "key" it emits (they match here).
    # A None score = ABSTAIN (evaluator didn't apply to this example): it is
    # SKIPPED — not counted toward the per-evaluator total, never a failure, and
    # rendered as "-" in the table. Only an explicit 0 is a failure.
    rows: list[tuple[str, str, dict[str, int | None]]] = []
    per_eval_pass: dict[str, int] = {k: 0 for k in eval_keys}
    per_eval_total: dict[str, int] = {k: 0 for k in eval_keys}
    failures = 0

    for res in results:
        example = res["example"]
        run = res["run"]
        meta = (example.metadata or {})
        mode = meta.get("expected_mode", "?")
        message = (example.inputs or {}).get("message", "")

        scores: dict[str, int | None] = {}
        for er in res["evaluation_results"]["results"]:
            key = er.key
            # Preserve None (abstain) distinctly from 0 (fail).
            score = None if er.score is None else int(er.score)
            scores[key] = score
            if score is None:
                continue  # abstain: not counted, not a failure.
            if key in per_eval_total:
                per_eval_total[key] += 1
                per_eval_pass[key] += score
            if score == 0:
                failures += 1

        rows.append((mode, message, scores))

    # Print table. Columns are derived from EVALUATORS + _COLUMN_LABELS (single
    # source of truth); labels are shortened to keep the row width readable now
    # that two LLM-judge columns are included. "-" = abstain (evaluator skipped).
    columns = [(k, *_COLUMN_LABELS[k]) for k in eval_keys]
    print("\n=== onsen-flow experiment results ===\n")
    header = (
        f"{'mode':<10} "
        + " ".join(f"{label:>{width}}" for _k, label, width in columns)
        + "  message"
    )
    print(header)
    print("-" * len(header))
    for mode, message, scores in rows:
        def cell(k: str) -> str:
            v = scores.get(k)
            return "PASS" if v == 1 else ("FAIL" if v == 0 else "-")

        cells = " ".join(f"{cell(k):>{width}}" for k, _label, width in columns)
        print(f"{mode:<10} {cells}  {message[:50]}")
    print("-" * len(header))

    print("\nPer-evaluator pass rate:")
    for k, _label, _width in columns:
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
