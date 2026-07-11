"""PR3b tests — TripSlots + the multi-turn elicit-loop.

The Done condition (docs/v3-trip-planner-plan.md §6 PR3b): multi-turn slot-filling
works. A message missing a required slot yields exactly ONE follow-up and ends the
turn; the NEXT turn on the SAME ``session_id`` resumes with the prior slots intact
and merges the new info; once all required slots are present, the flow proceeds past
elicitation to the discrete ``plan`` placeholder.

The extraction LLM is ALWAYS mocked — we patch the module-level
``agent.trip.slots._llm`` (same seam as the intent tests patch ``intent._llm``), so
the suite is deterministic and makes no real OpenAI calls. Each test drives the graph
with a per-turn ``SlotUpdate`` the mocked LLM "returns", then asserts the routing
(elicit vs plan), the reply, and that slots accumulated in the checkpoint.

A FRESH graph is built per test via ``build_trip_graph`` so its in-process
MemorySaver starts empty (the module-level ``trip_graph`` singleton is long-lived and
would carry state between tests).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.trip import slots as slots_module
from agent.trip.graph import PLAN_ACK_REPLY, build_trip_graph
from agent.trip.slots import (
    SlotUpdate,
    TripSlots,
    merge_slots,
    missing_required,
    next_question,
)


def _cfg(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _mock_llm(*updates: SlotUpdate) -> MagicMock:
    """Stand-in for the module-level structured-output ``_llm``.

    ``extract_slots`` awaits ``_llm.ainvoke`` once per turn; ``side_effect`` feeds a
    distinct ``SlotUpdate`` per call so a multi-turn conversation is fully scripted.
    """
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=list(updates))
    return llm


# --- pure slot logic (no graph, no LLM) -------------------------------------


def test_missing_required_order_and_emptiness():
    # Empty slots → all three required, in priority order.
    assert missing_required(TripSlots()) == ["regions", "nights", "dates_or_season"]
    # Fill regions only → the next two remain, still ordered.
    assert missing_required(TripSlots(regions=["Gifu"])) == ["nights", "dates_or_season"]
    # All required present → nothing missing (optionals have defaults).
    complete = TripSlots(regions=["Gifu"], nights=5, dates_or_season="autumn")
    assert missing_required(complete) == []


def test_next_question_targets_first_missing_and_is_none_when_complete():
    # First missing is regions → the regions question.
    q = next_question(TripSlots())
    assert q is not None and "area" in q.lower()
    # regions filled → question shifts to nights.
    assert "nights" in (next_question(TripSlots(regions=["Gifu"])) or "").lower()
    # Complete → no question.
    assert next_question(TripSlots(regions=["Gifu"], nights=3, dates_or_season="spring")) is None


def test_merge_preserves_prior_and_updates_only_mentioned():
    current = TripSlots(regions=["Gifu"], nights=5, dates_or_season="autumn", party="family")
    # A delta that mentions ONLY nights must not wipe regions/dates/party.
    merged = merge_slots(current, SlotUpdate(nights=6))
    assert merged.nights == 6
    assert merged.regions == ["Gifu"]
    assert merged.dates_or_season == "autumn"
    assert merged.party == "family"


def test_merge_empty_list_delta_does_not_wipe_prior_regions():
    current = TripSlots(regions=["Gifu", "Nagano"])
    # An all-None / empty-list delta ("message mentioned nothing") leaves regions.
    merged = merge_slots(current, SlotUpdate())
    assert merged.regions == ["Gifu", "Nagano"]


def test_trip_slots_defaults_match_plan_section_2():
    s = TripSlots()
    assert s.regions == [] and s.nights is None and s.dates_or_season is None
    assert s.party == "couple"
    assert s.budget == "mid"
    assert s.mobility_transport == "mixed"
    assert s.pace == "relaxed"
    assert s.spring_or_scenery_prefs == ""
    assert s.must_haves == []


# --- the core Done condition: partial → complete across two turns ------------


@pytest.mark.asyncio
async def test_partial_then_complete_across_two_turns_same_session():
    graph = build_trip_graph()
    session_id = "trip-partial-complete"

    # Turn 1: user gives regions + nights but no timing → still missing dates.
    turn1 = SlotUpdate(regions=["Gifu", "Nagano"], nights=5)
    # Turn 2 (same session): user supplies the season → now complete.
    turn2 = SlotUpdate(dates_or_season="autumn")

    with patch.object(slots_module, "_llm", _mock_llm(turn1, turn2)):
        r1 = await graph.ainvoke(
            {"message": "5 nights in Gifu and Nagano"}, config=_cfg(session_id)
        )
        r2 = await graph.ainvoke({"message": "in autumn"}, config=_cfg(session_id))

    # Turn 1 → elicit for the missing required slot (dates), NOT the plan ack.
    assert r1["reply"] != PLAN_ACK_REPLY
    assert "when" in r1["reply"].lower() or "season" in r1["reply"].lower()

    # Turn 2 → prior slots persisted (regions/nights), the season merged in, and the
    # flow proceeded PAST elicitation to the plan placeholder.
    assert r2["reply"] == PLAN_ACK_REPLY
    snap = graph.get_state(_cfg(session_id)).values
    assert snap["slots"]["regions"] == ["Gifu", "Nagano"]
    assert snap["slots"]["nights"] == 5
    assert snap["slots"]["dates_or_season"] == "autumn"
    # gather runs every turn → turn_count accumulated.
    assert snap["turn_count"] == 2


@pytest.mark.asyncio
async def test_complete_in_one_message_asks_no_question():
    graph = build_trip_graph()
    session_id = "trip-one-shot"
    full = SlotUpdate(regions=["Oita"], nights=3, dates_or_season="early November")

    with patch.object(slots_module, "_llm", _mock_llm(full)):
        r = await graph.ainvoke(
            {"message": "3 nights in Oita in early November"}, config=_cfg(session_id)
        )

    # All required present on the first message → straight to the plan placeholder,
    # no follow-up question asked.
    assert r["reply"] == PLAN_ACK_REPLY


@pytest.mark.asyncio
async def test_only_one_question_asked_per_turn():
    graph = build_trip_graph()
    session_id = "trip-one-question"
    # Nothing extracted → all three required missing, but only ONE follow-up allowed.
    with patch.object(slots_module, "_llm", _mock_llm(SlotUpdate())):
        r = await graph.ainvoke({"message": "I want an onsen trip"}, config=_cfg(session_id))

    reply = r["reply"]
    assert reply != PLAN_ACK_REPLY
    # Exactly one question mark → a single focused ask, not a barrage.
    assert reply.count("?") == 1
    # It targets the HIGHEST-priority missing slot (regions), not nights/dates.
    assert "area" in reply.lower()


@pytest.mark.asyncio
async def test_elicit_makes_no_llm_call_beyond_the_single_extraction():
    # The elicit branch must be cheap: gather runs the ONE extraction, elicit adds no
    # further LLM calls.
    graph = build_trip_graph()
    mock = _mock_llm(SlotUpdate(regions=["Gifu"]))  # missing nights + dates → elicit
    with patch.object(slots_module, "_llm", mock):
        await graph.ainvoke({"message": "onsen in Gifu"}, config=_cfg("trip-cheap"))
    mock.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_slots_isolated_per_session_id():
    graph = build_trip_graph()
    # Session A gathers two turns of state; session B starts fresh.
    updates = [
        SlotUpdate(regions=["Gifu"]),  # A turn 1
        SlotUpdate(nights=4),  # A turn 2
        SlotUpdate(regions=["Kyoto"]),  # B turn 1
    ]
    with patch.object(slots_module, "_llm", _mock_llm(*updates)):
        await graph.ainvoke({"message": "Gifu"}, config=_cfg("A"))
        await graph.ainvoke({"message": "4 nights"}, config=_cfg("A"))
        await graph.ainvoke({"message": "Kyoto"}, config=_cfg("B"))

    a = graph.get_state(_cfg("A")).values
    b = graph.get_state(_cfg("B")).values
    assert a["slots"]["regions"] == ["Gifu"] and a["slots"]["nights"] == 4
    assert a["turn_count"] == 2
    # B is independent: its own region, no leakage of A's nights, first turn only.
    assert b["slots"]["regions"] == ["Kyoto"]
    assert b["slots"]["nights"] is None
    assert b["turn_count"] == 1


# --- extract_slots merges onto the passed-in current slots -------------------


@pytest.mark.asyncio
async def test_extract_slots_merges_onto_current():
    current = TripSlots(regions=["Gifu"], nights=5)
    with patch.object(slots_module, "_llm", _mock_llm(SlotUpdate(dates_or_season="autumn"))):
        merged = await slots_module.extract_slots("in autumn", current)
    assert merged.regions == ["Gifu"]
    assert merged.nights == 5
    assert merged.dates_or_season == "autumn"
