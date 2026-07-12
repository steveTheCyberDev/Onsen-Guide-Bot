"""PR3c tests — the naive itinerary builder (the real ``plan`` node).

The Done condition (docs/v3-trip-planner-plan.md §6 PR3c): an end-to-end trip reply
for a COMPLETE request. Given all required slots (in one message or accumulated
across turns), the flow proceeds past elicitation and the discrete ``plan`` node
assembles a naive itinerary from REAL retrieved onsen — never fabricated. This file
covers:

  * complete slots → an itinerary whose onsen are real + in-region, with a coherent
    template reply and ``AgentResponse.onsens`` populated;
  * a region with NO ingested data → an explicit "no onsen found for X" (no
    fabrication), that region contributing zero onsen;
  * the elicit-loop still short-circuits when a required slot is missing (PR3b
    behaviour intact) — the plan node / retrieval is NOT reached.

Both external calls are ALWAYS mocked so the suite is deterministic and free:
  * the gather extraction LLM — patch ``agent.trip.slots._llm`` (same seam as PR3b);
  * the per-region retrieval — patch ``agent.trip.itinerary.query_onsen_structured``
    (the first ``services/`` call from ``agent/trip/``), so no real Chroma/OpenAI.

The graph-level tests build a FRESH graph per test via ``build_trip_graph`` so its
in-process MemorySaver starts empty; the ``plan_trip`` tests drive the module-level
singleton and use unique session ids to stay isolated.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.agent import OnsenResult
from agent.trip import itinerary as itinerary_module
from agent.trip import slots as slots_module
from agent.trip.agent import plan_trip
from agent.trip.graph import build_trip_graph
from agent.trip.itinerary import (
    allocate_nights,
    build_itinerary,
    build_reply,
    onsen_results_from_itinerary,
)
from agent.trip.slots import SlotUpdate


def _cfg(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _onsen(name: str, prefecture: str = "Gifu") -> dict:
    """A query_onsen_structured-shaped record for the given region."""
    return {
        "name": name,
        "location": f"Some City, {prefecture}",
        "spring_type": "Sulfur Spring",
        "spa_quality": f"{name} is a relaxing sulfur spring.",
        "detail_url": f"https://example.com/{name.replace(' ', '-')}",
        "lat": 36.0,
        "lng": 137.0,
    }


def _mock_llm(*updates: SlotUpdate) -> MagicMock:
    """Stand-in for the module-level structured-output extraction ``_llm``."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=list(updates))
    return llm


def _retrieval_by_region(mapping: dict[str, list[dict]]) -> MagicMock:
    """Mock ``query_onsen_structured`` keyed by the ``prefecture`` kwarg.

    ``retrieve_candidates`` calls the service once per region with
    ``prefecture=region``; this returns that region's records (or [] for a region
    absent from ``mapping`` — the no-data case).
    """

    def _fn(query, prefecture=None, n_results=20):
        return list(mapping.get(prefecture, []))

    return MagicMock(side_effect=_fn)


# --- pure allocation logic ---------------------------------------------------


def test_allocate_nights_even_and_front_loaded_remainder():
    assert allocate_nights(4, 2) == [2, 2]
    assert allocate_nights(5, 2) == [3, 2]  # remainder front-loaded
    assert allocate_nights(4, 3) == [2, 1, 1]
    assert allocate_nights(3, 1) == [3]


def test_allocate_nights_more_regions_than_nights_gives_zeros():
    # 1 night across 3 regions → first gets it, rest get 0 (still earn a day-trip
    # stop via max(1, ...) in build_itinerary).
    assert allocate_nights(1, 3) == [1, 0, 0]


def test_allocate_nights_zero_regions_is_empty():
    assert allocate_nights(5, 0) == []


# --- build_itinerary: deterministic assembly from real records ---------------


def test_build_itinerary_distributes_and_picks_by_pace():
    candidates = {
        "Gifu": [_onsen(f"Gifu {i}", "Gifu") for i in range(5)],
        "Nagano": [_onsen(f"Nagano {i}", "Nagano") for i in range(5)],
    }
    slots = {"regions": ["Gifu", "Nagano"], "nights": 5, "pace": "relaxed"}
    itin = build_itinerary(slots, candidates)

    assert itin["nights"] == 5
    legs = {leg["region"]: leg for leg in itin["regions"]}
    # 5 nights over 2 regions → [3, 2]; relaxed = 1 stop/night.
    assert legs["Gifu"]["nights"] == 3 and len(legs["Gifu"]["onsens"]) == 3
    assert legs["Nagano"]["nights"] == 2 and len(legs["Nagano"]["onsens"]) == 2
    # selected_onsens is the flat concatenation in region order.
    assert len(itin["selected_onsens"]) == 5
    assert [o["name"] for o in itin["selected_onsens"]][:3] == ["Gifu 0", "Gifu 1", "Gifu 2"]


def test_build_itinerary_packed_pace_picks_two_per_night():
    candidates = {"Gifu": [_onsen(f"Gifu {i}", "Gifu") for i in range(10)]}
    slots = {"regions": ["Gifu"], "nights": 3, "pace": "packed"}
    itin = build_itinerary(slots, candidates)
    # packed = 2 stops/night, 3 nights → up to 6 stops.
    assert len(itin["regions"][0]["onsens"]) == 6


def test_build_itinerary_caps_picks_at_available_candidates():
    # Wants 3 stops but only 1 candidate exists → picks 1, never pads.
    candidates = {"Gifu": [_onsen("Gero Onsen", "Gifu")]}
    slots = {"regions": ["Gifu"], "nights": 3, "pace": "relaxed"}
    itin = build_itinerary(slots, candidates)
    assert len(itin["regions"][0]["onsens"]) == 1


def test_build_itinerary_flags_no_data_region_and_omits_its_onsen():
    candidates = {"Gifu": [_onsen("Gero Onsen", "Gifu")]}  # Hokkaido absent → no data
    slots = {"regions": ["Gifu", "Hokkaido"], "nights": 4, "pace": "relaxed"}
    itin = build_itinerary(slots, candidates)

    legs = {leg["region"]: leg for leg in itin["regions"]}
    assert legs["Gifu"]["no_data"] is False and legs["Gifu"]["onsens"]
    # No-data region: flagged, zero onsen, never fabricated.
    assert legs["Hokkaido"]["no_data"] is True
    assert legs["Hokkaido"]["onsens"] == []
    # selected_onsens only ever contains real, in-region picks.
    assert all(o["name"] == "Gero Onsen" for o in itin["selected_onsens"])


# --- build_reply -------------------------------------------------------------


def test_build_reply_names_real_onsen_and_flags_no_data():
    candidates = {"Gifu": [_onsen("Gero Onsen", "Gifu")]}
    slots = {"regions": ["Gifu", "Hokkaido"], "nights": 2, "pace": "relaxed"}
    reply = build_reply(build_itinerary(slots, candidates))
    assert "itinerary" in reply.lower()
    assert "Gero Onsen" in reply
    assert "No onsen found for Hokkaido" in reply


def test_build_reply_all_regions_no_data_plans_nothing():
    slots = {"regions": ["Hokkaido", "Aomori"], "nights": 3, "pace": "relaxed"}
    reply = build_reply(build_itinerary(slots, {}))
    assert "couldn't find" in reply.lower()
    assert "Hokkaido" in reply and "Aomori" in reply


# --- projection onto OnsenResult (drops non-OnsenResult keys) -----------------


def test_onsen_results_projection_drops_detail_url_and_keeps_fields():
    itin = {"selected_onsens": [_onsen("Gero Onsen", "Gifu")]}
    results = onsen_results_from_itinerary(itin)
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, OnsenResult)
    assert r.name == "Gero Onsen"
    assert r.spring_type == "Sulfur Spring"
    assert r.lat == 36.0 and r.lng == 137.0
    # detail_url is not an OnsenResult field — projection must not have leaked it.
    assert not hasattr(r, "detail_url")


# --- end-to-end via the graph: complete request → itinerary ------------------


@pytest.mark.asyncio
async def test_complete_request_builds_itinerary_with_real_in_region_onsen():
    graph = build_trip_graph()
    session_id = "trip-3c-complete"
    full = SlotUpdate(regions=["Gifu", "Nagano"], nights=4, dates_or_season="autumn")
    retrieval = _retrieval_by_region(
        {
            "Gifu": [_onsen("Gero Onsen", "Gifu"), _onsen("Hirayu Onsen", "Gifu")],
            "Nagano": [_onsen("Nozawa Onsen", "Nagano")],
        }
    )

    with patch.object(slots_module, "_llm", _mock_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ):
        r = await graph.ainvoke(
            {"message": "4 nights in Gifu and Nagano this autumn"}, config=_cfg(session_id)
        )

    # Reached the plan node (an itinerary), not an elicit question.
    assert "itinerary" in r["reply"].lower()
    assert "?" not in r["reply"]
    # Retrieval was filtered per region/prefecture.
    called_prefectures = {c.kwargs["prefecture"] for c in retrieval.call_args_list}
    assert called_prefectures == {"Gifu", "Nagano"}

    # State accumulated real candidates + a structured itinerary.
    snap = graph.get_state(_cfg(session_id)).values
    assert len(snap["candidates"]) == 3
    itin = snap["itinerary"]
    selected_names = {o["name"] for o in itin["selected_onsens"]}
    assert selected_names <= {"Gero Onsen", "Hirayu Onsen", "Nozawa Onsen"}
    # Every selected onsen is real + in one of the requested regions.
    assert selected_names and not (selected_names - {"Gero Onsen", "Hirayu Onsen", "Nozawa Onsen"})


@pytest.mark.asyncio
async def test_region_with_no_data_says_none_found_and_fabricates_nothing():
    graph = build_trip_graph()
    session_id = "trip-3c-nodata"
    full = SlotUpdate(regions=["Gifu", "Hokkaido"], nights=3, dates_or_season="winter")
    # Only Gifu has ingested data; Hokkaido returns nothing.
    retrieval = _retrieval_by_region({"Gifu": [_onsen("Gero Onsen", "Gifu")]})

    with patch.object(slots_module, "_llm", _mock_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ):
        r = await graph.ainvoke(
            {"message": "3 nights in Gifu and Hokkaido in winter"}, config=_cfg(session_id)
        )

    assert "No onsen found for Hokkaido" in r["reply"]
    snap = graph.get_state(_cfg(session_id)).values
    legs = {leg["region"]: leg for leg in snap["itinerary"]["regions"]}
    assert legs["Hokkaido"]["no_data"] is True and legs["Hokkaido"]["onsens"] == []
    # No fabrication: every selected onsen is the real Gifu one.
    assert all(o["name"] == "Gero Onsen" for o in snap["itinerary"]["selected_onsens"])


# --- the elicit short-circuit is intact (PR3b) — plan/retrieval NOT reached --


@pytest.mark.asyncio
async def test_missing_required_slot_short_circuits_before_retrieval():
    graph = build_trip_graph()
    session_id = "trip-3c-elicit"
    # regions only → nights + dates still missing → elicit, never plan.
    retrieval = _retrieval_by_region({"Gifu": [_onsen("Gero Onsen", "Gifu")]})

    with patch.object(slots_module, "_llm", _mock_llm(SlotUpdate(regions=["Gifu"]))), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ):
        r = await graph.ainvoke({"message": "onsen in Gifu"}, config=_cfg(session_id))

    # An elicit follow-up, not an itinerary; retrieval was never called.
    assert "itinerary" not in r["reply"].lower()
    assert r["reply"].count("?") == 1
    retrieval.assert_not_called()
    # No itinerary written to state on an elicit turn.
    assert graph.get_state(_cfg(session_id)).values.get("itinerary") is None


# --- end-to-end via plan_trip: AgentResponse.onsens populated ----------------


@pytest.mark.asyncio
async def test_plan_trip_populates_agentresponse_onsens_on_a_plan_turn():
    full = SlotUpdate(regions=["Gifu"], nights=2, dates_or_season="autumn")
    retrieval = _retrieval_by_region(
        {"Gifu": [_onsen("Gero Onsen", "Gifu"), _onsen("Hirayu Onsen", "Gifu")]}
    )

    with patch.object(slots_module, "_llm", _mock_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ):
        resp = await plan_trip("2 nights in Gifu this autumn", session_id="trip-3c-plan-turn")

    # onsens populated with real, projected OnsenResults; hotels still empty.
    assert resp.onsens and all(isinstance(o, OnsenResult) for o in resp.onsens)
    assert {o.name for o in resp.onsens} <= {"Gero Onsen", "Hirayu Onsen"}
    assert resp.hotels == []
    assert resp.recommendation is None
    assert "itinerary" in resp.reply.lower()


@pytest.mark.asyncio
async def test_plan_trip_leaves_onsens_empty_on_an_elicit_turn():
    retrieval = _retrieval_by_region({"Gifu": [_onsen("Gero Onsen", "Gifu")]})
    with patch.object(slots_module, "_llm", _mock_llm(SlotUpdate(regions=["Gifu"]))), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ):
        resp = await plan_trip("onsen in Gifu", session_id="trip-3c-plan-elicit")

    # Elicit turn: a follow-up question, empty onsens, no retrieval.
    assert resp.onsens == []
    assert resp.reply.count("?") == 1
    retrieval.assert_not_called()
