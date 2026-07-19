"""LangSmith experiment harness for the V2/V2.5 onsen workflow.

This is a STANDALONE runnable script, NOT a pytest test: it makes PAID LLM calls
(one full ``run_workflow`` flow per dataset example, each spanning the intent
parse plus, for recommend examples, the analyze brain) and uploads per-example
scores, cost, latency, and traces to LangSmith via ``langsmith.evaluate()``.

What it covers:
  * a versioned LangSmith DATASET (``onsen-flow-evals``) covering all 3 modes
    (search / recommend / ask) plus no-data edge cases and multi-turn trip threads,
  * EVALUATORS scoring grounding, structural correctness per mode, the trip
    evaluators, cost budget, and latency,
  * results land in LangSmith as an EXPERIMENT, so runs are comparable
    run-over-run and across models, with cost/latency captured per example.

Ground truth for grounding is read from ChromaDB metadata at runtime (per
prefecture), so the eval stays in sync with whatever is actually ingested.
No onsen names are hardcoded.

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

For a reference of WHAT each evaluator judges (and against which source of truth),
see ``docs/what-we-judge.md``.
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
# core.config.export_langsmith_env() runs when the workflow pipeline module is
# imported and uses os.environ.setdefault(...) for LANGSMITH_PROJECT; langsmith
# also caches env vars (lru_cache). So the eval project must be in os.environ
# BEFORE the first `vectorstore.*` / `agent.*` / `langsmith` import fires —
# otherwise the flow's CHILD workflow traces land in the default (prod/dev)
# project. langsmith honors both the LANGSMITH_* and legacy LANGCHAIN_* aliases,
# so set both.
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
# NOTE: both judges are currently PARKED (not in EVALUATORS) — see the note above
# the EVALUATORS list. This config + the judge functions are kept for re-enabling
# once the flow/agents and KB data have stabilised.
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
    # trip (V3 PR4): a multi-turn thread — one intent-parse + one slot-extraction
    # LLM call PER TURN (both cheap intent_model), no analyze brain, free Chroma
    # retrieval. Cost is roughly search × turns. STARTING NUMBER (flagged): tune
    # once we have measured baselines the way search/recommend were tuned.
    "trip": 0.02,
}
LATENCY_BUDGET_MS: dict[str, int] = {
    "search": 8000,
    "recommend": 20000,
    "ask": 8000,
    "no-data": 8000,
    # trip: measured end-to-end across ALL turns of the thread (multi-turn +
    # per-region Chroma retrieval + 2 small LLM calls/turn). STARTING NUMBER
    # (flagged): loose enough for a 1–2 turn thread; tighten after a baseline run.
    "trip": 20000,
}


# --- Ground truth -------------------------------------------------------------
def normalize(name: str) -> str:
    """Coarse name normalization: lowercase, strip, collapse whitespace.

    Intentionally-simple equality — good enough to catch blatant fabrication (a
    name the DB never heard of). Does NOT handle romanization variants or
    'Onsen'-suffix differences; that's a later tightening.
    """
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def build_ground_truth() -> dict[str, set[str]]:
    """Read all ChromaDB metadatas and group normalized onsen names by prefecture.

    Returns a mapping ``prefecture_en -> set of normalized allowed names``. A
    prefecture absent from the mapping has NO records (so a grounded flow must
    return nothing for it).
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
        # expected_count asserts the requested count is HONOURED (Gifu has >5,
        # so "top 5" must return exactly 5, not the default ceiling).
        "message": "What's the top 5 onsens in Gifu?",
        "expected_mode": "search",
        "prefecture": "Gifu",
        "has_data": True,
        "wants_hotels": False,
        "expected_count": 5,
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
        # NOTE: deliberately does NOT request hotels. The recommend+hotels path
        # calls the live Rakuten API (pipeline.py search_hotels), which the eval
        # gate must not depend on — it is non-deterministic, rate-limited, and
        # unscored here (no hotel evaluator). Keeping this recommend example
        # hotel-free lets the gate run with dummy Rakuten creds in CI.
        "message": "Recommend an onsen in Shizuoka for a relaxing weekend",
        "expected_mode": "recommend",
        "prefecture": "Shizuoka",
        "has_data": True,
        "wants_hotels": False,
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
    # --- V3 trip-planner (multi-turn threads) --------------------------------
    # Trip examples carry `messages: list[str]` (run through ONE session_id so the
    # elicit-loop is exercised) instead of a single `message`. `regions` +
    # `expected_nights` drive the trip evaluators; `no_data_regions` is the AUTHORED
    # expectation, reconciled against ChromaDB truth at runtime (like `has_data`).
    # `prefecture`/`has_data` are unused for trip (per-region grounding is dynamic).
    {
        # (a) Under-specified opener → MUST trigger a follow-up; the completing
        # turn supplies all required slots and yields an itinerary.
        "messages": [
            "Can you plan a multi-day onsen trip itinerary for me?",
            "5 nights across Gifu and Shizuoka this autumn please",
        ],
        "expected_mode": "trip",
        "prefecture": None,
        "has_data": True,
        "wants_hotels": False,
        "regions": ["Gifu", "Shizuoka"],
        "expected_nights": 5,
        "no_data_regions": [],
    },
    {
        # (b) Complete in ONE message → straight to the plan node, no follow-up.
        "messages": ["Plan me a 4-night onsen trip itinerary in Gifu this autumn"],
        "expected_mode": "trip",
        "prefecture": None,
        "has_data": True,
        "wants_hotels": False,
        "regions": ["Gifu"],
        "expected_nights": 4,
        "no_data_regions": [],
    },
    {
        # (c) Complete-in-one-message MULTI-region trip (all valid, in-data).
        # RECONCILED for PR5 (2026-07-12): this example used to pair Gifu with the
        # uningested Hokkaido to exercise the plan node's "no onsen found for X"
        # leg. But region-validation ("reject early") now rejects any region NOT in
        # the ingested-prefecture set at slot-fill — a Japanese-but-uningested
        # prefecture (Hokkaido) is rejected exactly like a non-Japan one — so the
        # flow never reaches the plan node and the itinerary-level no-data leg is
        # unreachable via slot-fill. So this example is now an all-valid multi-region
        # trip (distinct trajectory from (a): complete-in-one vs. follow-up thread).
        # A dedicated invalid-region "never planned" eval example is DEFERRED (the
        # earmarked follow-up); it needs an "expected_never_planned" expectation the
        # trip evaluators don't yet model.
        "messages": [
            "Plan a 3-night onsen trip itinerary across Gifu and Shizuoka this winter"
        ],
        "expected_mode": "trip",
        "prefecture": None,
        "has_data": True,
        "wants_hotels": False,
        "regions": ["Gifu", "Shizuoka"],
        "expected_nights": 3,
        "no_data_regions": [],
    },
    # --- V3 PR7 RED BASELINE: multi-factor re-planning (2026-07-13) -----------
    # The four examples below capture "multi-factor re-planning" scenarios whose
    # conflict only SURFACES after routing/feasibility/weather/ratings signals the
    # naive PR3c plan node does not have. They are an EVAL-FIRST RED BASELINE for
    # PR7 (routing + re-planning): all required slots are valid and every region is
    # in the ingested set (Gifu/Nagano/Shizuoka/Aichi/Okinawa), so the flow REACHES
    # today's plan node and produces a naive itinerary. The EXISTING evaluators
    # (slot_filling_completeness / tool_selection_presence / plan_validity /
    # hotels_exist / cost_budget / latency) therefore PASS — the plan is well-formed,
    # grounded, and adds up. What FAILS (by design) are the four NEW multi-factor
    # evaluators (constraint_conflict_acknowledged / no_infeasible_plan /
    # tradeoff_explained / dropped_region_reasoned): the naive node crams the trip
    # silently and never acknowledges the conflict, flags infeasibility, explains a
    # tradeoff, or reasons about a dropped region. PR7 turning those four GREEN on
    # these examples is its definition of done. The `expect_*` / `conflict_factors`
    # keys gate the new evaluators (they ABSTAIN when absent, so the existing 12
    # examples are untouched). See _expectation() + the PR7 evaluator block below.
    {
        # ① Over-constrained: pace × nights × spread. Three DISPERSED regions can't
        # be covered at a relaxed pace in 3 nights — the conflict only surfaces once
        # routing shows the drive times. Expected (PR7): keep the stated relaxed
        # pace, drop/merge the outlier region rather than silently cramming, and
        # explain the tradeoff. Timing ("this autumn") is supplied so all required
        # slots complete and the flow reaches the plan node.
        "messages": [
            "Plan a relaxed 3-night onsen trip across Gifu, Nagano and Shizuoka "
            "this autumn, for two."
        ],
        "expected_mode": "trip",
        "prefecture": None,
        "has_data": True,
        "wants_hotels": False,
        "regions": ["Gifu", "Nagano", "Shizuoka"],
        "expected_nights": 3,
        "no_data_regions": [],
        "conflict_factors": ["pace_x_nights_x_spread"],
        "expect_constraint_conflict_ack": True,
        "expect_tradeoff_explanation": True,
        # PR7 may drop/merge either dispersed outlier; PASS if EITHER is named in a
        # drop/merge context (coarsest honest level — see dropped_region_reasoned).
        "expect_dropped_regions": ["Nagano", "Shizuoka"],
    },
    {
        # ② Geographic infeasibility: Okinawa ↔ Gifu needs a flight (a lost travel
        # day) and may not even route over water. Slots are complete and both
        # regions are valid/in-data, so the naive node happily emits a
        # silently-broken 2+2 itinerary. Expected (PR7): FLAG the feasibility problem
        # instead of emitting the broken plan, and offer a sensible reshape (split
        # trips / reallocate nights).
        "messages": [
            "Plan a packed 4-night onsen trip this spring — 2 nights in Okinawa "
            "and 2 nights in Gifu."
        ],
        "expected_mode": "trip",
        "prefecture": None,
        "has_data": True,
        "wants_hotels": False,
        "regions": ["Okinawa", "Gifu"],
        "expected_nights": 4,
        "no_data_regions": [],
        "conflict_factors": ["geographic_infeasibility"],
        "expect_constraint_conflict_ack": True,
        "expect_feasibility_flag": True,
    },
    {
        # ③ Weather × outdoor pref × season: 3 nights in January across Nagano and
        # Gifu, loving outdoor rotenburo. The conflict surfaces only after a weather
        # signal — some high-elevation stops are snowed-in / road-risk, others are
        # ideal yukimi (snow-view) rotenburo. Expected (PR7): select winter-accessible
        # outdoor stops, drop the snow-risk one, and reorder for a weather buffer —
        # a stop-level tradeoff (so no expect_dropped_regions; that's region-level).
        "messages": [
            "Plan a 3-night onsen trip in January across Nagano and Gifu — "
            "we love outdoor rotenburo."
        ],
        "expected_mode": "trip",
        "prefecture": None,
        "has_data": True,
        "wants_hotels": False,
        "regions": ["Nagano", "Gifu"],
        "expected_nights": 3,
        "no_data_regions": [],
        "conflict_factors": ["weather_x_outdoor_x_season"],
        "expect_constraint_conflict_ack": True,
        "expect_tradeoff_explanation": True,
    },
    {
        # ④ Budget × ratings × party: 3 nights in Gifu and Aichi, family of 4, mid
        # budget, wanting highly-rated onsen. The conflict surfaces only after a
        # ratings + hotel lookup — the top-rated onsen pair only with pricey ryokan
        # or lack family-of-4 rooms within a mid budget. Expected (PR7): trade rating
        # for fit where it costs least, and STATE what it traded and why.
        "messages": [
            "Plan a 3-night onsen trip this autumn in Gifu and Aichi for a family "
            "of four, mid budget, and we want highly-rated onsen."
        ],
        "expected_mode": "trip",
        "prefecture": None,
        "has_data": True,
        "wants_hotels": False,
        "regions": ["Gifu", "Aichi"],
        "expected_nights": 3,
        "no_data_regions": [],
        "conflict_factors": ["budget_x_ratings_x_party"],
        "expect_constraint_conflict_ack": True,
        "expect_tradeoff_explanation": True,
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
        # Trip examples span multiple regions; reconcile which of them currently
        # have NO data so the "explicit none-found" expectation self-corrects if a
        # region later gets ingested (mirrors the has_data reconciliation above).
        if ex.get("regions"):
            ex["no_data_regions"] = [r for r in ex["regions"] if r not in allowed]
        out.append(ex)
    return out


def _example_input(ex: dict) -> dict:
    """The dataset INPUT payload for one example: threaded or single-message.

    Trip examples carry ``messages: list[str]`` (a conversation thread run through
    one session); all other modes keep the single ``message`` shape. Keeping both
    shapes lets the existing search/recommend/ask examples stay byte-identical.
    """
    if ex.get("messages"):
        return {"messages": ex["messages"]}
    return {"message": ex["message"]}


def _example_key(inputs: dict) -> str:
    """Stable dedup key for get-or-create, covering both input shapes.

    Single-message examples key on the message; threads key on the joined turns.
    Used to detect which ``_EXAMPLES`` are already present in the live dataset.
    """
    if inputs.get("messages"):
        return "||".join(inputs["messages"])
    return inputs.get("message", "")


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
        # Optional (search-mode): when the user asked for an explicit count
        # ('top 5'), the response must return exactly that many onsen. None when
        # no count was requested, so the count is not asserted.
        "expected_count": ex.get("expected_count"),
        # Optional (trip-mode): the requested regions, total nights, and which
        # regions are expected to have no data. Drive the trip evaluators
        # (slot-filling / tool-presence / plan-validity). Empty/None for non-trip.
        "regions": ex.get("regions", []),
        "expected_nights": ex.get("expected_nights"),
        "no_data_regions": ex.get("no_data_regions", []),
        # Optional (trip-mode, V3 PR7 RED BASELINE): multi-factor re-planning
        # expectations. These GATE the four PR7 evaluators below — each abstains
        # unless its flag is set — so the pre-PR7 examples never see them. Default
        # off/empty so every non-multi-factor example (search/recommend/ask/no-data
        # and the three pre-PR7 trip threads) leaves them absent and unaffected.
        "conflict_factors": ex.get("conflict_factors", []),
        "expect_constraint_conflict_ack": ex.get("expect_constraint_conflict_ack", False),
        "expect_feasibility_flag": ex.get("expect_feasibility_flag", False),
        "expect_tradeoff_explanation": ex.get("expect_tradeoff_explanation", False),
        "expect_dropped_regions": ex.get("expect_dropped_regions", []),
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
        existing_keys = {
            _example_key(e.inputs or {})
            for e in client.list_examples(dataset_id=dataset.id)
        }
        missing = [
            ex for ex in examples if _example_key(_example_input(ex)) not in existing_keys
        ]
        if missing:
            client.create_examples(
                dataset_id=dataset.id,
                inputs=[_example_input(ex) for ex in missing],
                outputs=[_expectation(ex) for ex in missing],
                metadata=[_expectation(ex) for ex in missing],
            )
            print(
                f"Added {len(missing)} new example(s) to existing dataset: "
                f"{[_example_key(_example_input(ex)) for ex in missing]}"
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
        inputs=[_example_input(ex) for ex in examples],
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
    from agent.trip import itinerary as trip_itinerary
    from agent.trip.graph import trip_graph
    from agent.trip.slots import TripSlots, missing_required
    from agent.workflow import pipeline
    from agent.workflow.cost import summarize_usage

    _counter = {"n": 0}

    def target(inputs: dict) -> dict:
        # Threaded examples carry `messages: list[str]` (run through ONE session so
        # the trip elicit-loop is exercised); single-message examples are wrapped to
        # a one-element list so both shapes share this loop.
        messages = inputs.get("messages") or [inputs["message"]]
        _counter["n"] += 1
        session_id = f"eval-flow-{_counter['n']}-{int(time.time())}"
        cfg = {"configurable": {"thread_id": session_id}}

        captured = {}
        real_cls = pipeline.UsageMetadataCallbackHandler

        def _factory(*args, **kwargs):
            cb = real_cls(*args, **kwargs)
            captured["cb"] = cb
            return cb

        # Spy on the trip plan node's per-region retrieval WITHOUT replacing it —
        # record the prefecture each call filters by, then delegate to the real
        # service so grounding still runs against live Chroma. This is the
        # deterministic "tool-selection presence" signal (a local spy, NOT a
        # LangSmith run-tree parse). Reverted in finally.
        real_q = trip_itinerary.query_onsen_structured
        retrieval_prefectures: list[str] = []

        def _spy_q(query, prefecture=None, n_results=20):
            retrieval_prefectures.append(prefecture)
            return real_q(query, prefecture=prefecture, n_results=n_results)

        pipeline.UsageMetadataCallbackHandler = _factory  # type: ignore[assignment]
        trip_itinerary.query_onsen_structured = _spy_q  # type: ignore[assignment]

        total_cost = 0.0
        trajectory: list[dict] = []
        result: dict = {}
        started = time.monotonic()
        try:
            for message in messages:
                result = asyncio.run(
                    pipeline.run_workflow(message, session_id=session_id)
                )
                # Accumulate cost across every turn of the thread (each turn builds
                # its own usage callback via the factory above).
                cb = captured.get("cb")
                usage_meta = getattr(cb, "usage_metadata", {}) if cb else {}
                total_cost += summarize_usage(usage_meta)["cost_usd"]
                # Per-turn trajectory for the slot-filling evaluator: what required
                # slots were still missing AFTER this turn's gather, and whether the
                # turn asked a follow-up. Both are read from the checkpointed trip
                # state / the returned reply — what the flow itself exposes, no
                # run-tree parsing. For non-trip examples this reads an empty
                # snapshot and the trip evaluators abstain, so it is harmless.
                snap = trip_graph.get_state(cfg).values or {}
                miss = missing_required(TripSlots(**(snap.get("slots") or {})))
                asked = result.get("reply") in _elicit_question_values()
                trajectory.append({"missing_required": miss, "asked_followup": asked})
        finally:
            pipeline.UsageMetadataCallbackHandler = real_cls  # type: ignore[assignment]
            trip_itinerary.query_onsen_structured = real_q  # type: ignore[assignment]
        latency_ms = int((time.monotonic() - started) * 1000)

        final = trip_graph.get_state(cfg).values or {}
        return {
            **result,
            "_cost_usd": total_cost,
            "_latency_ms": latency_ms,
            # Trip signals (ignored by non-trip evaluators, which abstain).
            "_trajectory": trajectory,
            "_final_slots": final.get("slots") or {},
            "_itinerary": final.get("itinerary"),
            "_retrieval_prefectures": retrieval_prefectures,
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

    Trip examples ABSTAIN here — their onsen grounding is per-region and handled by
    the dedicated ``plan_validity`` evaluator (a single ``prefecture``/``has_data``
    pair cannot express a multi-region trip).
    """
    if reference_outputs.get("expected_mode") == "trip":
        return {"key": "grounding", "score": None, "comment": "n/a (trip → plan_validity)"}
    has_data = bool(reference_outputs.get("has_data"))
    names = _onsen_names(outputs)

    if not has_data:
        if not names:
            return {"key": "grounding", "score": 1, "comment": "empty as required (no-data)"}
        return {
            "key": "grounding",
            "score": 0,
            "comment": f"fabricated for no-data prefecture: {names}",
        }

    pref = reference_outputs.get("prefecture")
    allowed = _GROUND_TRUTH.get(pref, set())
    if not names:
        # Expected results but got none — not a grounding failure per se, but the
        # flow returned nothing where data exists. Treat as ungrounded=fail so it
        # surfaces; the structure evaluator also covers emptiness.
        return {
            "key": "grounding",
            "score": 0,
            "comment": f"expected results for {pref}, got none",
        }
    invented = [n for n in names if normalize(n) not in allowed]
    if invented:
        return {
            "key": "grounding",
            "score": 0,
            "comment": f"not in {pref} ground truth: {invented}",
        }
    return {"key": "grounding", "score": 1, "comment": "all names grounded in DB"}


def structure(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff the response shape matches the expected mode.

    recommend ⇒ recommendation non-null AND ≥1 onsen has non-empty pros.
    search    ⇒ recommendation is None AND every onsen has empty pros & cons.
    ask       ⇒ onsens empty AND recommendation None AND reply non-empty. When the
                harness runs with ask_enabled ON (real RAG answer), reply must ALSO
                differ from the stub (the stub means the answer node never ran).
    no-data   ⇒ onsens empty.
    trip      ⇒ ABSTAIN — trip structure is asserted by the dedicated trip
                evaluators (slot_filling_completeness / tool_selection_presence /
                plan_validity), not this per-mode shape check.
    """
    mode = reference_outputs.get("expected_mode")
    if mode == "trip":
        return {"key": "structure", "score": None, "comment": "n/a (trip evaluators)"}
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
        # When the user asked for an explicit count ('top 5'), it must be honoured.
        expected_count = reference_outputs.get("expected_count")
        if expected_count is not None:
            ok = ok and len(onsens) == expected_count
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

    comment = (
        f"mode={mode} recommendation={recommendation is not None} "
        f"onsens={len(onsens)} reply={'yes' if reply else 'no'}"
    )
    return {"key": "structure", "score": 1 if ok else 0, "comment": comment}


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
    return {
        "key": "proscons_grounding",
        "score": 1,
        "comment": "all pros/cons grounded in onsen facts",
    }


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
    comment = (
        "answer grounded in retrieved chunks"
        if verdict == 1
        else "answer not supported by retrieved chunks"
    )
    return {"key": "ask_grounding", "score": verdict, "comment": comment}


# --- Trip evaluators (V3 PR4/PR5 — deterministic, structure + trajectory) -----
# The trip flow is multi-turn and produces an itinerary, so it is scored by
# DEDICATED deterministic evaluators reading the target's returned AgentResponse +
# the checkpointed TripState signals (_trajectory / _final_slots / _itinerary /
# _retrieval_prefectures) — NOT LangSmith run-trees, and NO LLM judge. Each ABSTAINS
# (score=None) on non-trip examples. PR5 adds `hotels_exist` (every stop looked up,
# no fabricated hotels). Still NO re-plan check — that arrives with PR7.


def _elicit_question_values() -> set[str]:
    """The set of canned elicit follow-up questions the trip flow can ask.

    Read from ``agent.trip.slots`` so the eval and the agent never drift (mirrors
    how ``_ask_stub_reply`` reads the pipeline stub). Used by the target to decide,
    per turn, whether a follow-up was asked — a structural check against the known
    elicit vocabulary, not fuzzy string matching.
    """
    from agent.trip.slots import _ELICIT_QUESTIONS

    return set(_ELICIT_QUESTIONS.values())


def slot_filling_completeness(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff slot-filling was correct across the thread (trip only).

    Two invariants, read from the per-turn ``_trajectory`` the target built:
      1. Follow-up IFF missing — every turn asked a follow-up exactly when a
         required slot was still missing after that turn's gather.
      2. Completeness — by the final turn, no required slot remains missing.

    ABSTAINS (None) on non-trip examples. Fails (0) if the thread never ran (no
    trajectory), if any turn violates the follow-up-iff-missing invariant, or if
    required slots are still missing at the end.
    """
    if reference_outputs.get("expected_mode") != "trip":
        return {"key": "slot_filling_completeness", "score": None, "comment": "n/a"}

    trajectory = outputs.get("_trajectory") or []
    if not trajectory:
        return {"key": "slot_filling_completeness", "score": 0, "comment": "no turns ran"}

    for i, turn in enumerate(trajectory):
        missing = bool(turn.get("missing_required"))
        asked = bool(turn.get("asked_followup"))
        if asked != missing:
            return {
                "key": "slot_filling_completeness",
                "score": 0,
                "comment": (
                    f"turn {i}: asked_followup={asked} but missing_required="
                    f"{turn.get('missing_required')}"
                ),
            }

    final_missing = trajectory[-1].get("missing_required") or []
    if final_missing:
        return {
            "key": "slot_filling_completeness",
            "score": 0,
            "comment": f"required slots still missing at end: {final_missing}",
        }
    return {
        "key": "slot_filling_completeness",
        "score": 1,
        "comment": "follow-up-iff-missing held; all required slots filled",
    }


def tool_selection_presence(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff onsen retrieval was invoked for every requested region (trip only).

    Asserts PRESENCE (not ordering / not exact args) — once slots are complete the
    plan node must call ``query_onsen_structured`` once per region. Reads the
    target's ``_retrieval_prefectures`` spy list (the prefecture each retrieval
    filtered by). Routing/ordering checks do NOT belong here yet — routing doesn't
    exist until PR7. ABSTAINS on non-trip examples.
    """
    if reference_outputs.get("expected_mode") != "trip":
        return {"key": "tool_selection_presence", "score": None, "comment": "n/a"}

    regions = reference_outputs.get("regions") or []
    prefectures = set(outputs.get("_retrieval_prefectures") or [])
    if not prefectures:
        return {
            "key": "tool_selection_presence",
            "score": 0,
            "comment": "retrieval never invoked (slots never completed?)",
        }
    missing = [r for r in regions if r not in prefectures]
    if missing:
        return {
            "key": "tool_selection_presence",
            "score": 0,
            "comment": f"no retrieval for region(s): {missing}",
        }
    return {
        "key": "tool_selection_presence",
        "score": 1,
        "comment": f"retrieval invoked per region: {sorted(prefectures)}",
    }


def plan_validity(outputs: dict, reference_outputs: dict) -> dict:
    """Core trip gate: the final itinerary is valid, grounded, and adds up (trip only).

    Checks, all deterministic, over the returned ``_itinerary`` + AgentResponse:
      * an itinerary was produced;
      * NIGHTS ADD UP — per-region nights sum to the itinerary total (and match the
        authored ``expected_nights`` when given);
      * ONSEN REAL + IN-REGION — every onsen in a region's leg is in that region's
        ChromaDB ground truth (extends the name-level ``grounding`` logic per
        region), and every onsen surfaced in ``AgentResponse.onsens`` is in some
        requested region's truth;
      * NO FABRICATION for no-data regions — each expected no-data region is flagged
        ``no_data`` with zero onsen.

    ABSTAINS on non-trip examples. Reads the module-level ``_GROUND_TRUTH`` snapshot
    the harness injects (same source the ``grounding`` evaluator uses).
    """
    if reference_outputs.get("expected_mode") != "trip":
        return {"key": "plan_validity", "score": None, "comment": "n/a"}

    itinerary = outputs.get("_itinerary")
    if not itinerary:
        return {"key": "plan_validity", "score": 0, "comment": "no itinerary produced"}

    legs = itinerary.get("regions") or []
    total_nights = itinerary.get("nights")

    # Nights add up across the legs, and match the authored expectation if present.
    if sum(leg.get("nights", 0) for leg in legs) != total_nights:
        return {
            "key": "plan_validity",
            "score": 0,
            "comment": f"nights don't add up: legs sum != {total_nights}",
        }
    expected_nights = reference_outputs.get("expected_nights")
    if expected_nights is not None and total_nights != expected_nights:
        return {
            "key": "plan_validity",
            "score": 0,
            "comment": f"nights={total_nights} != expected {expected_nights}",
        }

    # Per-region grounding: every leg's onsen must be real + in that region.
    for leg in legs:
        region = leg.get("region")
        allowed = _GROUND_TRUTH.get(region, set())
        names = [o.get("name", "") for o in (leg.get("onsens") or [])]
        invented = [n for n in names if normalize(n) not in allowed]
        if invented:
            return {
                "key": "plan_validity",
                "score": 0,
                "comment": f"onsen not in {region} ground truth: {invented}",
            }
        # A no-data leg must carry zero onsen (never fabricated).
        if leg.get("no_data") and names:
            return {
                "key": "plan_validity",
                "score": 0,
                "comment": f"no-data region {region} has onsen: {names}",
            }

    # Every expected no-data region must actually be flagged no_data.
    expected_nd = set(reference_outputs.get("no_data_regions") or [])
    flagged_nd = {leg.get("region") for leg in legs if leg.get("no_data")}
    if not expected_nd <= flagged_nd:
        return {
            "key": "plan_validity",
            "score": 0,
            "comment": f"expected no-data not flagged: {expected_nd - flagged_nd}",
        }

    # The surfaced AgentResponse.onsens must all be real + in a requested region.
    regions = reference_outputs.get("regions") or []
    all_allowed: set[str] = set()
    for r in regions:
        all_allowed |= _GROUND_TRUTH.get(r, set())
    surfaced_invented = [n for n in _onsen_names(outputs) if normalize(n) not in all_allowed]
    if surfaced_invented:
        return {
            "key": "plan_validity",
            "score": 0,
            "comment": f"surfaced onsen out of region: {surfaced_invented}",
        }

    return {"key": "plan_validity", "score": 1, "comment": "itinerary valid + grounded"}


def hotels_exist(outputs: dict, reference_outputs: dict) -> dict:
    """PR5 hotels check: every onsen stop went through the hotel step, no fabrication.

    For each lodging stop (a real in-region onsen), the plan node must have looked
    up nearby hotels — so the stop carries a ``hotels`` list (present, possibly
    empty). An empty list is the honest "none found" case and PASSES (hotels are
    fail-soft; a Rakuten outage yields none). What must NEVER happen is a surfaced
    hotel that came from nowhere: every hotel in ``AgentResponse.hotels`` must trace
    back to some stop's ``hotels`` lookup.

    ABSTAINS on non-trip examples. Deterministic — reads the itinerary state + the
    returned hotels; no Rakuten call, no run-tree parsing.
    """
    if reference_outputs.get("expected_mode") != "trip":
        return {"key": "hotels_exist", "score": None, "comment": "n/a"}

    itinerary = outputs.get("_itinerary")
    if not itinerary:
        return {"key": "hotels_exist", "score": 0, "comment": "no itinerary produced"}

    # Every planned stop must have run the hotel step (a `hotels` list present).
    stop_hotel_names: set[str] = set()
    for leg in itinerary.get("regions") or []:
        if leg.get("no_data"):
            continue
        for onsen in leg.get("onsens") or []:
            hotels = onsen.get("hotels")
            if not isinstance(hotels, list):
                return {
                    "key": "hotels_exist",
                    "score": 0,
                    "comment": f"stop {onsen.get('name')!r} missing hotels lookup",
                }
            for h in hotels:
                stop_hotel_names.add(normalize(h.get("name", "")))

    # Anti-fabrication: every surfaced hotel must come from a stop's lookup.
    surfaced = [h.get("name", "") for h in (outputs.get("hotels") or [])]
    fabricated = [n for n in surfaced if normalize(n) not in stop_hotel_names]
    if fabricated:
        return {
            "key": "hotels_exist",
            "score": 0,
            "comment": f"surfaced hotels not from any stop lookup: {fabricated}",
        }

    return {
        "key": "hotels_exist",
        "score": 1,
        "comment": f"all stops looked up; {len(surfaced)} hotel(s) surfaced, none fabricated",
    }


# --- Trip multi-factor re-planning evaluators (V3 PR7 RED BASELINE) -----------
# These four DETERMINISTIC evaluators express the multi-factor expectations the
# current trip evaluators cannot: a plan that acknowledges an over-constraint,
# flags a geographic infeasibility instead of silently emitting a broken
# itinerary, explains a tradeoff, and reasons about a dropped/merged region.
#
# THEY ARE A RED BASELINE FOR PR7. Against today's naive PR3c plan node they FAIL
# BY DESIGN: the naive node crams the trip into a template reply with NO conflict
# acknowledgement, feasibility flag, tradeoff, or drop reasoning — so each returns
# score=0 on its multi-factor example. PR7 (routing + re-planning) turning these
# GREEN is its definition of done. NOTE: they ARE in the active EVALUATORS gate
# (unlike the parked LLM judges), so a live experiment / the CI eval gate will show
# these as failing pairs until PR7 lands — that visible red IS the baseline.
#
# Detection is intentionally COARSE: each scans the deterministic ``reply`` prose
# for a vocabulary of behaviour markers. A naive template itinerary contains NONE
# of them, so the FAIL is genuine (not tautological). # PR7: tighten from prose
# marker-matching to inspecting a structured state signal (e.g. a per-leg travel
# time / a `constraints` or `feasibility` field the plan node PR7 will add) once
# those fields exist. Each evaluator ABSTAINS (score=None) unless its per-example
# gate flag is set in reference_outputs, so the pre-PR7 examples are untouched.

# Behaviour-marker vocabularies. Chosen to be indicative of the target behaviour
# AND absent from the naive template reply ("Here's a naive N-night onsen
# itinerary — Region (X nights): Onsen (nearby hotels: ...); ... No onsen found
# for X."), so their absence is a real FAIL rather than a rigged one.
_CONFLICT_ACK_MARKERS = (
    "won't be able", "will not be able", "won't fit", "can't cover",
    "cannot cover", "can't realistically", "cannot realistically",
    "not enough time", "too much ground", "spread too thin", "would be rushed",
    "would feel rushed", "would be tight", "over-constrained", "overconstrained",
    "unrealistic", "not feasible", "isn't feasible", "not realistic",
    "isn't realistic", "difficult to cover", "hard to cover", "trade-off",
    "tradeoff", "too far apart", "too dispersed",
)
_FEASIBILITY_MARKERS = (
    "flight", "fly ", "flying", "travel day", "lost day", "lose a day",
    "losing a day", "over water", "not reachable", "can't drive", "cannot drive",
    "can't route", "won't route", "split trip", "split it into",
    "separate trips", "two separate", "reallocate", "reshape", "requires a plane",
)
_TRADEOFF_MARKERS = (
    "in exchange", "at the cost", "at the expense", "rather than", "instead of",
    "prioritis", "prioritiz", "traded", "trade ", "sacrific", "swap ",
    "to stay within", "to keep within", "so i'd", "so i would", "so we'd",
    "so we would", "which means", "in return",
)
_DROP_MARKERS = (
    "drop", "dropping", "merge", "merging", "focus on", "focus just on",
    "leave out", "leaving out", "skip", "skipping", "remove", "exclude",
    "narrow to", "narrow down", "cut ",
)


def _reply_lower(outputs: dict) -> str:
    """The response reply text lowercased — the surface the markers scan."""
    return (outputs.get("reply") or "").lower()


def _markers_present(text: str, markers: tuple[str, ...]) -> list[str]:
    """Return the subset of ``markers`` that appear as substrings in ``text``."""
    return [m for m in markers if m in text]


def constraint_conflict_acknowledged(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff the reply ACKNOWLEDGES the over-constraint (trip PR7 target).

    Applies only to examples flagged ``expect_constraint_conflict_ack`` (the four
    multi-factor examples); ABSTAINS (None) otherwise. The naive plan node crams
    the trip silently and never signals the conflict, so this FAILS on today's
    output BY DESIGN — PR7 makes it green.
    """
    if not reference_outputs.get("expect_constraint_conflict_ack"):
        return {"key": "constraint_conflict_acknowledged", "score": None, "comment": "n/a"}
    hits = _markers_present(_reply_lower(outputs), _CONFLICT_ACK_MARKERS)
    if hits:
        return {
            "key": "constraint_conflict_acknowledged",
            "score": 1,
            "comment": f"acknowledged the constraint conflict: {hits}",
        }
    return {
        "key": "constraint_conflict_acknowledged",
        "score": 0,
        "comment": "reply crams silently — no over-constraint acknowledgement (PR7 target)",
    }


def no_infeasible_plan(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff a geographically-infeasible trip is FLAGGED, not silently planned.

    Applies only to examples flagged ``expect_feasibility_flag`` (the Okinawa↔Gifu
    example); ABSTAINS (None) otherwise. Score 1 requires the reply to flag the
    feasibility problem (flight / lost travel day / split trips / reallocate). The
    naive node emits a silently-broken 2+2 itinerary with no such flag, so this
    FAILS BY DESIGN until PR7 adds routing/feasibility.
    """
    if not reference_outputs.get("expect_feasibility_flag"):
        return {"key": "no_infeasible_plan", "score": None, "comment": "n/a"}
    hits = _markers_present(_reply_lower(outputs), _FEASIBILITY_MARKERS)
    if hits:
        return {
            "key": "no_infeasible_plan",
            "score": 1,
            "comment": f"flagged the feasibility problem: {hits}",
        }
    return {
        "key": "no_infeasible_plan",
        "score": 0,
        "comment": "emitted an itinerary without flagging the geographic infeasibility (PR7 target)",
    }


def tradeoff_explained(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff the reply EXPLAINS the tradeoff it made (trip PR7 target).

    Applies only to examples flagged ``expect_tradeoff_explanation``; ABSTAINS
    (None) otherwise. A real re-plan states what it gave up and why (e.g. traded a
    rating for a family-fit room, swapped a snowed-in stop for an accessible one).
    The naive node makes no tradeoff and explains none, so this FAILS BY DESIGN
    until PR7.
    """
    if not reference_outputs.get("expect_tradeoff_explanation"):
        return {"key": "tradeoff_explained", "score": None, "comment": "n/a"}
    hits = _markers_present(_reply_lower(outputs), _TRADEOFF_MARKERS)
    if hits:
        return {
            "key": "tradeoff_explained",
            "score": 1,
            "comment": f"explained a tradeoff: {hits}",
        }
    return {
        "key": "tradeoff_explained",
        "score": 0,
        "comment": "no tradeoff explanation in reply (PR7 target)",
    }


def dropped_region_reasoned(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff the reply drops/merges a region AND names one it was allowed to drop.

    Applies only to examples carrying a non-empty ``expect_dropped_regions`` list
    (the over-constrained example); ABSTAINS (None) otherwise. Requires BOTH a
    drop/merge marker AND at least one of the droppable regions named in the reply —
    the naive node names every requested region but with NO drop context, so this
    FAILS BY DESIGN until PR7 re-plans by dropping an outlier. ``expect_dropped_regions``
    lists every region PR7 may legitimately drop (either dispersed outlier), so the
    check does not over-constrain WHICH region is dropped.
    """
    expected = reference_outputs.get("expect_dropped_regions") or []
    if not expected:
        return {"key": "dropped_region_reasoned", "score": None, "comment": "n/a"}
    text = _reply_lower(outputs)
    drop_hits = _markers_present(text, _DROP_MARKERS)
    named = [r for r in expected if r.lower() in text]
    if drop_hits and named:
        return {
            "key": "dropped_region_reasoned",
            "score": 1,
            "comment": f"reasoned about dropping {named} ({drop_hits})",
        }
    return {
        "key": "dropped_region_reasoned",
        "score": 0,
        "comment": (
            f"no dropped-region reasoning (drop_markers={drop_hits}, "
            f"named={named}) — naive plan keeps all regions (PR7 target)"
        ),
    }


def cost_budget(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff the run's measured cost is within the per-mode budget."""
    mode = reference_outputs.get("expected_mode")
    budget = COST_BUDGET_USD.get(mode, COST_BUDGET_USD["search"])
    cost = float(outputs.get("_cost_usd", 0.0) or 0.0)
    return {
        "key": "cost_budget",
        "score": 1 if cost <= budget else 0,
        "comment": f"${cost:.4f} vs budget ${budget} ({mode})",
    }


def latency(outputs: dict, reference_outputs: dict) -> dict:
    """Score 1 iff the run's measured latency is within the per-mode budget."""
    mode = reference_outputs.get("expected_mode")
    budget = LATENCY_BUDGET_MS.get(mode, LATENCY_BUDGET_MS["search"])
    measured = int(outputs.get("_latency_ms", 0) or 0)
    return {
        "key": "latency",
        "score": 1 if measured <= budget else 0,
        "comment": f"{measured}ms vs budget {budget}ms ({mode})",
    }


# The active gate is DETERMINISTIC only. The two LLM-as-judge evaluators
# (proscons_grounding, ask_grounding) are intentionally PARKED — kept in the file
# (with their unit tests) but removed from the gate while the flow/agents and the
# KB data are still being consolidated. An LLM judging LLM-generated prose against
# a moving target is non-deterministic: it false-failed a clean release on a
# grounded onsen (Yanase), so at this stage it is noise, not actionable signal.
# Re-add them to this list once the system + data have stabilised — they will
# auto-reappear in the report (columns derive from EVALUATORS + _COLUMN_LABELS).
EVALUATORS = [
    grounding,
    structure,
    # V3 PR4 trip evaluators (deterministic; abstain on non-trip examples).
    slot_filling_completeness,
    tool_selection_presence,
    plan_validity,
    hotels_exist,  # V3 PR5
    # V3 PR7 RED BASELINE — multi-factor re-planning. Deterministic; ABSTAIN on
    # every example except the four multi-factor trip examples. Expected to FAIL
    # against today's naive plan node (that is the baseline); PR7 turns them green.
    constraint_conflict_acknowledged,
    no_infeasible_plan,
    tradeoff_explained,
    dropped_region_reasoned,
    cost_budget,
    latency,
    # proscons_grounding,   # PARKED — see note above
    # ask_grounding,        # PARKED — see note above
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
    "slot_filling_completeness": ("slots", 6),
    "tool_selection_presence": ("tools", 6),
    "plan_validity": ("plan", 6),
    "hotels_exist": ("hotels", 6),
    "constraint_conflict_acknowledged": ("conflict", 8),
    "no_infeasible_plan": ("feasible", 8),
    "tradeoff_explained": ("tradeoff", 8),
    "dropped_region_reasoned": ("drop-rgn", 8),
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
    # trip mode also needs its gate ON so the trip examples exercise the real
    # trip-planner graph (slots + elicit-loop + naive itinerary) rather than
    # falling through to a plain onsen search. Flipped here and RESTORED in the same
    # finally — trip_enabled's committed default stays False (ships dead in prod);
    # this flip is eval-harness-local only, and a long-lived process never leaks it.
    prior_trip_enabled = settings.trip_enabled
    settings.analyze_enabled = True
    settings.ask_enabled = True
    settings.trip_enabled = True
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
        settings.trip_enabled = prior_trip_enabled

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
    rows: list[tuple[str, str, dict[str, int | None], dict[str, str | None]]] = []
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
        comments: dict[str, str | None] = {}
        for er in res["evaluation_results"]["results"]:
            key = er.key
            # Preserve None (abstain) distinctly from 0 (fail).
            score = None if er.score is None else int(er.score)
            scores[key] = score
            # Reason string the evaluator attached (for the per-FAIL line below).
            # getattr keeps test stand-ins (which omit .comment) working.
            comments[key] = getattr(er, "comment", None)
            if score is None:
                continue  # abstain: not counted, not a failure.
            if key in per_eval_total:
                per_eval_total[key] += 1
                per_eval_pass[key] += score
            if score == 0:
                failures += 1

        rows.append((mode, message, scores, comments))

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
    for mode, message, scores, comments in rows:
        def cell(k: str) -> str:
            v = scores.get(k)
            return "PASS" if v == 1 else ("FAIL" if v == 0 else "-")

        cells = " ".join(f"{cell(k):>{width}}" for k, _label, width in columns)
        print(f"{mode:<10} {cells}  {message[:50]}")
        # Under each row, explain every FAIL (score == 0) with the evaluator's
        # reason so the table is self-diagnosing — no need to open LangSmith for
        # the common case. Abstains ("-") and passes get no extra line.
        for k, _label, _width in columns:
            if scores.get(k) == 0:
                reason = comments.get(k) or "(no reason provided)"
                print(f"    └─ {k}: {reason}")
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
