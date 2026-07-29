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
  * ③ weather×outdoor×season (Nagano/Gifu, January) → RED (no weather signal):
    constraint_conflict_acknowledged, tradeoff_explained. # PR7-later: weather.
  * ④ budget×ratings×party (Gifu/Aichi, family) → RED (no ratings signal):
    constraint_conflict_acknowledged, tradeoff_explained. # PR7-later: ratings.

The evaluators are the SAME functions the LangSmith gate runs (imported from
``scripts.eval_flow``), scoring 1 / 0 by scanning the reply for behaviour markers —
so a green here means the plan node genuinely emitted the honest explanatory prose,
not that a keyword was stuffed.
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
_REF = {
    "over": {
        "expected_mode": "trip",
        "expect_constraint_conflict_ack": True,
        "expect_feasibility_flag": False,
        "expect_tradeoff_explanation": True,
        "expect_dropped_regions": ["Nagano", "Shizuoka"],
    },
    "infeasible": {
        "expected_mode": "trip",
        "expect_constraint_conflict_ack": True,
        "expect_feasibility_flag": True,
        "expect_tradeoff_explanation": False,
        "expect_dropped_regions": [],
    },
    "weather": {
        "expected_mode": "trip",
        "expect_constraint_conflict_ack": True,
        "expect_feasibility_flag": False,
        "expect_tradeoff_explanation": True,
        "expect_dropped_regions": [],
    },
    "budget": {
        "expected_mode": "trip",
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
async def test_example3_weather_stays_red_no_signal():
    # HONEST boundary: no weather signal in coordinates → the naive plan reply for a
    # compact 2-region winter trip carries no conflict prose → these stay RED (0).
    outputs = await _run(
        SlotUpdate(
            regions=["Nagano", "Gifu"], nights=3, dates_or_season="January",
            spring_or_scenery_prefs="outdoor rotenburo",
        ),
        "Plan a 3-night onsen trip in January across Nagano and Gifu — we love outdoor rotenburo.",
    )
    ref = _REF["weather"]
    assert _score(eval_flow.constraint_conflict_acknowledged, outputs, ref) == 0
    assert _score(eval_flow.tradeoff_explained, outputs, ref) == 0


@pytest.mark.asyncio
async def test_example4_budget_ratings_stays_red_no_signal():
    # HONEST boundary: no ratings signal → compact 2-region trip → RED (0).
    outputs = await _run(
        SlotUpdate(
            regions=["Gifu", "Aichi"], nights=3, dates_or_season="autumn",
            party="family", budget="mid", spring_or_scenery_prefs="highly-rated onsen",
        ),
        "Plan a 3-night onsen trip this autumn in Gifu and Aichi for a family of four, mid budget, highly-rated.",
    )
    ref = _REF["budget"]
    assert _score(eval_flow.constraint_conflict_acknowledged, outputs, ref) == 0
    assert _score(eval_flow.tradeoff_explained, outputs, ref) == 0
