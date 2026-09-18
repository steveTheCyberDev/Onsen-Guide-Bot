"""PR7 evaluator-outcome test — the four multi-factor examples ①–④ (V3 PR7).

Drives the REAL trip graph (retrieval + hotels mocked, so free + deterministic) on
each of the four PR7 red-baseline examples from ``scripts/eval_flow.py``, then calls
the four NEW evaluator functions DIRECTLY on the produced reply. This asserts the
ACTUAL honest outcome of the deterministic slice — which evaluators PR7 turns green
and which genuinely stay red because the signal (weather / ratings) does not exist:

  * ① over-constrained (pace×nights×spread, Gifu/Nagano/Shizuoka) → GREEN:
    constraint_conflict_acknowledged, tradeoff_explained, dropped_region_reasoned.
  * ② geographic infeasibility (Okinawa+Gifu) → GREEN:
    constraint_conflict_acknowledged, no_infeasible_plan.
  * ③ weather×outdoor×season (Nagano/Gifu, January) → ABSTAIN (no weather signal).
  * ④ budget×ratings×party (Gifu/Aichi, family) → ABSTAIN (no ratings signal).

The evaluators are the SAME functions the LangSmith gate runs (imported from
``scripts.eval_flow``), scoring 1 / 0 by scanning the reply for behaviour markers —
so a green here means the plan node genuinely emitted the honest explanatory prose,
not that a keyword was stuffed.

③ and ④ ABSTAIN rather than fail. Their ``conflict_factor`` is not in
``eval_flow._IMPLEMENTED_CONFLICT_FACTORS`` (no weather service; the ratings gate is
a separate product decision), so the four PR7 evaluators return score=None — an
honest "not ready" rather than failing the release gate on a capability that was
never built. ``_REF`` below therefore carries ``conflict_factors`` verbatim from the
dataset: WITHOUT it ``_factors_ready()`` passes vacuously and these tests would
assert an outcome the live gate never produces.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.trip import itinerary as itinerary_module
from agent.trip import slots as slots_module
from agent.trip.graph import build_trip_graph
from agent.trip.slots import SlotUpdate
from scripts import eval_flow

# Region coord fixtures (rough prefecture centroids) — the deterministic haversine
# rules run on these, so the drop/infeasible verdicts are reproducible.
_COORDS = {
    "Gifu": (35.8, 137.0),
    "Nagano": (36.6, 138.2),
    "Shizuoka": (34.9, 138.4),
    "Okinawa": (26.2, 127.7),
    "Aichi": (35.0, 137.0),
}


def _onsen(name: str, prefecture: str) -> dict:
    lat, lng = _COORDS[prefecture]
    return {
        "name": name,
        "location": f"City, {prefecture}",
        "spring_type": "Sulfur Spring",
        "spa_quality": f"{name} is a sulfur spring.",
        "detail_url": f"https://example.com/{name.replace(' ', '-')}",
        "lat": lat,
        "lng": lng,
    }


def _retrieval() -> MagicMock:
    """Return a couple of coord-bearing onsen for any requested region."""

    def _fn(query, prefecture=None, n_results=20):
        if prefecture in _COORDS:
            return [
                _onsen(f"{prefecture} Onsen A", prefecture),
                _onsen(f"{prefecture} Onsen B", prefecture),
            ]
        return []

    return MagicMock(side_effect=_fn)


def _mock_llm(update: SlotUpdate) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=update)
    return llm


async def _run(update: SlotUpdate, message: str) -> dict:
    """Drive a fresh graph for one complete-in-one-message trip; return AgentResponse-ish."""
    graph = build_trip_graph()
    retrieval = _retrieval()
    with patch.object(slots_module, "_llm", _mock_llm(update)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(itinerary_module, "search_hotels", return_value=[]):
        r = await graph.ainvoke({"message": message}, config={"configurable": {"thread_id": message}})
    return {"reply": r["reply"], "onsens": [], "hotels": []}


# Reference-output expectation flags, mirroring the four PR7 examples in eval_flow.
# ``conflict_factors`` is copied VERBATIM from each dataset example because the four
# PR7 evaluators gate on it via ``_factors_ready()``: a ref that omits the key passes
# that gate vacuously, so the outcome asserted here would diverge from the live gate.
_REF = {
    "over": {
        "expected_mode": "trip",
        "conflict_factors": ["pace_x_nights_x_spread"],
        "expect_constraint_conflict_ack": True,
        "expect_feasibility_flag": False,
        "expect_tradeoff_explanation": True,
        "expect_dropped_regions": ["Nagano", "Shizuoka"],
    },
    "infeasible": {
        "expected_mode": "trip",
        "conflict_factors": ["geographic_infeasibility"],
        "expect_constraint_conflict_ack": True,
        "expect_feasibility_flag": True,
        "expect_tradeoff_explanation": False,
        "expect_dropped_regions": [],
    },
    "weather": {
        "expected_mode": "trip",
        "conflict_factors": ["weather_x_outdoor_x_season"],
        "expect_constraint_conflict_ack": True,
        "expect_feasibility_flag": False,
        "expect_tradeoff_explanation": True,
        "expect_dropped_regions": [],
    },
    "budget": {
        "expected_mode": "trip",
        "conflict_factors": ["budget_x_ratings_x_party"],
        "expect_constraint_conflict_ack": True,
        "expect_feasibility_flag": False,
        "expect_tradeoff_explanation": True,
        "expect_dropped_regions": [],
    },
}


def _score(fn, outputs, ref) -> int | None:
    return fn(outputs, ref)["score"]


@pytest.mark.asyncio
async def test_example1_over_constrained_turns_three_evaluators_green():
    outputs = await _run(
        SlotUpdate(
            regions=["Gifu", "Nagano", "Shizuoka"], nights=3,
            dates_or_season="autumn", pace="relaxed", party="couple",
        ),
        "Plan a relaxed 3-night onsen trip across Gifu, Nagano and Shizuoka this autumn, for two.",
    )
    ref = _REF["over"]
    assert _score(eval_flow.constraint_conflict_acknowledged, outputs, ref) == 1
    assert _score(eval_flow.tradeoff_explained, outputs, ref) == 1
    assert _score(eval_flow.dropped_region_reasoned, outputs, ref) == 1
    # no_infeasible_plan abstains (not a feasibility example).
    assert _score(eval_flow.no_infeasible_plan, outputs, ref) is None


@pytest.mark.asyncio
async def test_example2_geographic_infeasibility_turns_two_evaluators_green():
    outputs = await _run(
        SlotUpdate(
            regions=["Okinawa", "Gifu"], nights=4, dates_or_season="spring", pace="packed",
        ),
        "Plan a packed 4-night onsen trip this spring — 2 nights in Okinawa and 2 nights in Gifu.",
    )
    ref = _REF["infeasible"]
    assert _score(eval_flow.constraint_conflict_acknowledged, outputs, ref) == 1
    assert _score(eval_flow.no_infeasible_plan, outputs, ref) == 1
    # tradeoff / dropped abstain (their gate flags are off for this example).
    assert _score(eval_flow.tradeoff_explained, outputs, ref) is None
    assert _score(eval_flow.dropped_region_reasoned, outputs, ref) is None


@pytest.mark.asyncio
async def test_example3_weather_abstains_until_a_weather_signal_exists():
    # HONEST boundary, two halves:
    #  (a) the LIVE gate outcome — weather_x_outdoor_x_season is not in
    #      _IMPLEMENTED_CONFLICT_FACTORS, so the evaluators ABSTAIN (None) rather
    #      than red-failing the release gate on a capability nobody built; and
    #  (b) the reason the abstain is honest — with the readiness gate lifted, the
    #      reply genuinely scores 0, i.e. the deterministic slice really does emit
    #      no conflict prose for a compact 2-region winter trip. Without (b) this
    #      test would still pass if the plan node started fabricating weather prose.
    outputs = await _run(
        SlotUpdate(
            regions=["Nagano", "Gifu"], nights=3, dates_or_season="January",
            spring_or_scenery_prefs="outdoor rotenburo",
        ),
        "Plan a 3-night onsen trip in January across Nagano and Gifu — we love outdoor rotenburo.",
    )
    ref = _REF["weather"]
    assert _score(eval_flow.constraint_conflict_acknowledged, outputs, ref) is None
    assert _score(eval_flow.tradeoff_explained, outputs, ref) is None

    ready = {k: v for k, v in ref.items() if k != "conflict_factors"}
    assert _score(eval_flow.constraint_conflict_acknowledged, outputs, ready) == 0
    assert _score(eval_flow.tradeoff_explained, outputs, ready) == 0


@pytest.mark.asyncio
async def test_example4_budget_ratings_abstains_until_the_factor_is_allowlisted():
    # Half (a) — the live-gate ABSTAIN — is the same as ③ and is what this test
    # is for. NOTE: unlike weather, the ratings SIGNAL now exists (Places ratings
    # ship in the projection); budget_x_ratings_x_party is still outside
    # _IMPLEMENTED_CONFLICT_FACTORS as a separate product decision. When that
    # factor is allowlisted this test fails on the `is None` half — the intended
    # prompt to re-baseline example ④, not a false alarm.
    #
    # KNOWN GAP (pre-existing, tracked separately — do not read half (b) as proof
    # of plan-node behaviour here): "Aichi" is absent from conftest's
    # TEST_KNOWN_PREFECTURES, so this turn is rejected by region validation and
    # never reaches the plan node. The reply is a region-elicit question, so the
    # `== 0` half below is true because nothing was planned at all — NOT because a
    # planned 2-region itinerary honestly lacks ratings prose (which is what ③
    # does demonstrate). Fixing it means widening the fixture's known set.
    outputs = await _run(
        SlotUpdate(
            regions=["Gifu", "Aichi"], nights=3, dates_or_season="autumn",
            party="family", budget="mid", spring_or_scenery_prefs="highly-rated onsen",
        ),
        "Plan a 3-night onsen trip this autumn in Gifu and Aichi for a family of four, mid budget, highly-rated.",
    )
    ref = _REF["budget"]
    assert _score(eval_flow.constraint_conflict_acknowledged, outputs, ref) is None
    assert _score(eval_flow.tradeoff_explained, outputs, ref) is None

    ready = {k: v for k, v in ref.items() if k != "conflict_factors"}
    assert _score(eval_flow.constraint_conflict_acknowledged, outputs, ready) == 0
    assert _score(eval_flow.tradeoff_explained, outputs, ready) == 0
