"""Region validation at slot-filling (2026-07-12 "reject early").

Unknown / non-Japan regions are rejected at the slot layer — BEFORE the plan node
— rather than surfacing as an itinerary "no onsen found for X" footnote. The mixed
case ("Gifu and Texas") must NOT quietly build the Gifu-only part: any invalid
region turns the turn into a region-elicit turn.

Two layers are covered:
  * pure slot helpers (``invalid_regions`` / ``region_invalid_message`` /
    ``should_elicit`` / ``elicit_message``) with a fabricated known set; and
  * the graph end-to-end — the extraction LLM (``slots._llm``) and the known set
    (``graph.known_prefectures``) are ALWAYS mocked, so no real OpenAI/Chroma. The
    plan-node retrieval is mocked on the turns that reach it.

The conftest autouse fixture already patches ``graph.known_prefectures`` to a
realistic set; tests here re-patch it explicitly so the valid/invalid split is
visible at the call site and independent of that default.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.trip import graph as graph_module
from agent.trip import itinerary as itinerary_module
from agent.trip import slots as slots_module
from agent.trip.graph import build_trip_graph
from agent.trip.slots import (
    SlotUpdate,
    TripSlots,
    elicit_message,
    invalid_regions,
    region_invalid_message,
    should_elicit,
)

# A fixed known set for these tests: Japanese prefectures we "ingested"; Texas /
# California are deliberately absent so they read as invalid.
KNOWN = frozenset({"Gifu", "Nagano", "Shizuoka", "Oita"})


def _cfg(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _mock_llm(*updates: SlotUpdate) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=list(updates))
    return llm


def _onsen(name: str) -> dict:
    return {
        "name": name,
        "location": "Some City, Gifu",
        "spring_type": "Sulfur Spring",
        "spa_quality": f"{name} description.",
        "detail_url": f"https://example.com/{name}",
        "lat": 36.0,
        "lng": 137.0,
    }


def _mock_retrieval(*records: dict) -> MagicMock:
    return MagicMock(return_value=list(records))


# --- pure helpers ------------------------------------------------------------


def test_invalid_regions_is_case_insensitive_and_preserves_order():
    slots = TripSlots(regions=["gifu", "TEXAS", "Nagano", "california"])
    assert invalid_regions(slots, KNOWN) == ["TEXAS", "california"]
    # All-valid → empty, regardless of case.
    assert invalid_regions(TripSlots(regions=["GIFU", "nagano"]), KNOWN) == []
    # Empty regions → nothing to validate (that's a *missing* case, not invalid).
    assert invalid_regions(TripSlots(), KNOWN) == []


def test_region_invalid_message_single_names_it_and_suggests_real_prefectures():
    msg = region_invalid_message(["Texas"], KNOWN)
    assert msg.startswith("Texas isn't somewhere I cover")
    assert "only plan Japanese onsen trips" in msg
    # Suggestions are drawn from the real known set (sorted) — never one we lack.
    assert "Gifu" in msg
    assert "Texas" not in msg.split("(e.g.")[1]  # not suggested as an example


def test_region_invalid_message_multiple_lists_all_with_aren_t():
    msg = region_invalid_message(["Texas", "California"], KNOWN)
    assert msg.startswith("Texas and California aren't somewhere I cover")


def test_should_elicit_true_on_invalid_region_even_when_all_slots_filled():
    complete_but_invalid = TripSlots(regions=["Gifu", "Texas"], nights=4, dates_or_season="autumn")
    assert should_elicit(complete_but_invalid, KNOWN) is True
    complete_valid = TripSlots(regions=["Gifu"], nights=4, dates_or_season="autumn")
    assert should_elicit(complete_valid, KNOWN) is False


def test_elicit_message_precedence_region_invalid_over_other_missing():
    # regions present but invalid, and nights/dates also missing → the region
    # message wins (regions is the highest-priority slot).
    slots = TripSlots(regions=["Texas"])
    msg = elicit_message(slots, KNOWN)
    assert msg is not None and "somewhere I cover" in msg
    # regions missing entirely → the original "which area(s)?" ask, not the
    # invalid-region message.
    missing = elicit_message(TripSlots(), KNOWN)
    assert missing is not None and "area" in missing.lower()
    # all valid + complete → None (route to plan).
    assert elicit_message(TripSlots(regions=["Gifu"], nights=3, dates_or_season="autumn"), KNOWN) is None


# --- graph end-to-end --------------------------------------------------------


@pytest.mark.asyncio
async def test_single_invalid_region_rejected_before_plan():
    graph = build_trip_graph()
    session_id = "region-single-invalid"
    # Everything filled but the region is California (invalid).
    update = SlotUpdate(regions=["California"], nights=3, dates_or_season="autumn")

    with patch.object(slots_module, "_llm", _mock_llm(update)), patch.object(
        graph_module, "known_prefectures", return_value=KNOWN
    ):
        r = await graph.ainvoke(
            {"message": "3 night onsen trip in California this autumn"},
            config=_cfg(session_id),
        )

    assert "California isn't somewhere I cover" in r["reply"]
    # Plan node NOT reached: no itinerary/candidates written.
    snap = graph.get_state(_cfg(session_id)).values
    assert snap.get("itinerary") is None
    assert not snap.get("candidates")


@pytest.mark.asyncio
async def test_mixed_valid_and_invalid_does_not_build_partial_itinerary():
    graph = build_trip_graph()
    session_id = "region-mixed"
    update = SlotUpdate(regions=["Gifu", "Texas"], nights=4, dates_or_season="autumn")
    retrieval = _mock_retrieval(_onsen("Gero Onsen"))

    with patch.object(slots_module, "_llm", _mock_llm(update)), patch.object(
        graph_module, "known_prefectures", return_value=KNOWN
    ), patch.object(itinerary_module, "query_onsen_structured", retrieval):
        r = await graph.ainvoke(
            {"message": "4 nights across Gifu and Texas"}, config=_cfg(session_id)
        )

    # Names the invalid region; does NOT build the Gifu part.
    assert "Texas isn't somewhere I cover" in r["reply"]
    snap = graph.get_state(_cfg(session_id)).values
    assert snap.get("itinerary") is None
    assert not snap.get("candidates")
    # Critically: retrieval was never invoked — no partial (Gifu-only) itinerary.
    retrieval.assert_not_called()


@pytest.mark.asyncio
async def test_all_valid_single_region_reaches_plan():
    graph = build_trip_graph()
    session_id = "region-valid-single"
    update = SlotUpdate(regions=["Gifu"], nights=3, dates_or_season="autumn")
    retrieval = _mock_retrieval(_onsen("Gero Onsen"))

    with patch.object(slots_module, "_llm", _mock_llm(update)), patch.object(
        graph_module, "known_prefectures", return_value=KNOWN
    ), patch.object(itinerary_module, "query_onsen_structured", retrieval):
        r = await graph.ainvoke(
            {"message": "3 nights in Gifu this autumn"}, config=_cfg(session_id)
        )

    assert "itinerary" in r["reply"].lower()
    snap = graph.get_state(_cfg(session_id)).values
    assert snap.get("itinerary") is not None
    retrieval.assert_called_once()


@pytest.mark.asyncio
async def test_all_valid_multi_region_reaches_plan_no_regression():
    graph = build_trip_graph()
    session_id = "region-valid-multi"
    update = SlotUpdate(regions=["Gifu", "Nagano"], nights=4, dates_or_season="autumn")
    retrieval = _mock_retrieval(_onsen("Gero Onsen"))

    with patch.object(slots_module, "_llm", _mock_llm(update)), patch.object(
        graph_module, "known_prefectures", return_value=KNOWN
    ), patch.object(itinerary_module, "query_onsen_structured", retrieval):
        r = await graph.ainvoke(
            {"message": "4 nights across Gifu and Nagano this autumn"},
            config=_cfg(session_id),
        )

    assert "itinerary" in r["reply"].lower()
    assert graph.get_state(_cfg(session_id)).values.get("itinerary") is not None


@pytest.mark.asyncio
async def test_missing_regions_entirely_gives_original_area_ask():
    graph = build_trip_graph()
    session_id = "region-missing"
    # Nothing extracted → regions missing entirely (not invalid).
    with patch.object(slots_module, "_llm", _mock_llm(SlotUpdate())), patch.object(
        graph_module, "known_prefectures", return_value=KNOWN
    ):
        r = await graph.ainvoke({"message": "I want an onsen trip"}, config=_cfg(session_id))

    assert "area" in r["reply"].lower()
    assert "somewhere I cover" not in r["reply"]  # NOT the invalid-region message


@pytest.mark.asyncio
async def test_correction_terminates_invalid_then_valid_reaches_plan():
    """TERMINATION: an invalid region turn, then a valid correction → plan reached,
    and the stale invalid region does not persist."""
    graph = build_trip_graph()
    session_id = "region-correction"
    # Turn 1: all slots filled but region is Texas (invalid) → region-elicit.
    turn1 = SlotUpdate(regions=["Texas"], nights=4, dates_or_season="autumn")
    # Turn 2: user corrects to Gifu — the extraction returns the FULL intended list
    # (["Gifu"]), which merge_slots REPLACES over ["Texas"], dropping the stale one.
    turn2 = SlotUpdate(regions=["Gifu"])
    retrieval = _mock_retrieval(_onsen("Gero Onsen"))

    with patch.object(slots_module, "_llm", _mock_llm(turn1, turn2)), patch.object(
        graph_module, "known_prefectures", return_value=KNOWN
    ), patch.object(itinerary_module, "query_onsen_structured", retrieval):
        r1 = await graph.ainvoke(
            {"message": "4 nights in Texas this autumn"}, config=_cfg(session_id)
        )
        r2 = await graph.ainvoke({"message": "make it Gifu instead"}, config=_cfg(session_id))

    # Turn 1 rejected; no itinerary.
    assert "Texas isn't somewhere I cover" in r1["reply"]
    # Turn 2 proceeds to plan; nights/dates persisted, Texas replaced by Gifu.
    assert "itinerary" in r2["reply"].lower()
    snap = graph.get_state(_cfg(session_id)).values
    assert snap["slots"]["regions"] == ["Gifu"]  # stale Texas gone
    assert snap["slots"]["nights"] == 4 and snap["slots"]["dates_or_season"] == "autumn"
    assert snap.get("itinerary") is not None
