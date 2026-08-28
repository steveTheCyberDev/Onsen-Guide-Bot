"""PR3a LangGraph skeleton tests — checkpointer persistence + readiness properties.

Originally pinned PR3a's inert canned-reply behaviour. PR3b reshaped the graph
(``START → gather → {elicit | plan} → END``) so ``gather`` now runs a structured-
output extraction LLM every turn; the old "canned reply, no LLM call" assumptions no
longer hold. What SURVIVES unchanged from PR3a and is still pinned here:

  * the four re-planning-readiness properties from docs/v3-trip-planner-plan.md §0
    (discrete ``plan`` node, compiled-with-checkpointer, MemorySaver default,
    Postgres guarded); and
  * the checkpointer persistence PROOF — working state accumulates across two turns
    of the SAME ``session_id`` (``turn_count`` bumped by ``gather`` every turn).

The behavioural turn-count tests now mock the extraction LLM (``slots._llm``) so the
suite stays deterministic and free — the elicit-loop specifics live in
``test_trip_slots_elicit_loop.py``.

Each behavioural test builds a FRESH graph via ``build_trip_graph`` so the in-process
MemorySaver starts empty (the module-level ``trip_graph`` singleton is shared and
would carry state between tests).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.trip import slots as slots_module
from agent.trip.agent import plan_trip
from agent.trip.graph import _build_checkpointer, build_trip_graph
from agent.trip.slots import SlotUpdate


def _cfg(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _mock_llm() -> MagicMock:
    # Every turn extracts an EMPTY delta — these tests care about turn_count
    # accumulation, not slot content, so any number of turns is covered.
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=SlotUpdate())
    return llm


# --- the core Done condition: two-turn state persistence --------------------


@pytest.mark.asyncio
async def test_state_persists_and_accumulates_across_two_turns_same_session():
    # Fresh graph → empty MemorySaver, so turn_count is deterministic.
    graph = build_trip_graph()
    session_id = "trip-session-A"

    with patch.object(slots_module, "_llm", _mock_llm()):
        # Turn 1: no prior checkpoint → gather sees turn_count absent, returns 1.
        r1 = await graph.ainvoke(
            {"message": "5 nights in Gifu"}, config=_cfg(session_id)
        )
        assert r1["turn_count"] == 1

        # Turn 2, SAME thread_id: gather must OBSERVE the accumulated state left by
        # turn 1 (turn_count==1) and increment to 2. This is the checkpointer proof.
        r2 = await graph.ainvoke(
            {"message": "make it mountain views"}, config=_cfg(session_id)
        )
        assert r2["turn_count"] == 2

    # And the checkpointer actually holds it, keyed by thread_id.
    snapshot = graph.get_state(_cfg(session_id))
    assert snapshot.values["turn_count"] == 2


@pytest.mark.asyncio
async def test_state_is_isolated_per_session_id():
    # Different thread_ids must NOT share accumulated state.
    graph = build_trip_graph()

    with patch.object(slots_module, "_llm", _mock_llm()):
        await graph.ainvoke({"message": "trip A"}, config=_cfg("sess-A"))
        await graph.ainvoke({"message": "trip A2"}, config=_cfg("sess-A"))
        rb = await graph.ainvoke({"message": "trip B"}, config=_cfg("sess-B"))

    # sess-B is on its first turn regardless of sess-A's two turns.
    assert rb["turn_count"] == 1
    assert graph.get_state(_cfg("sess-A")).values["turn_count"] == 2


# --- re-planning-readiness properties (§0) ----------------------------------


def test_graph_has_discrete_plan_node_not_react():
    # Property #3: a discrete ``plan`` node PR3c makes real / PR7 hangs a back-edge
    # off. Assert the node exists by name so a later reshape is a visible break.
    graph = build_trip_graph()
    assert "plan" in graph.get_graph().nodes


def test_graph_is_compiled_with_a_checkpointer():
    # Property #4: compiled WITH a checkpointer (that's what enables persistence).
    graph = build_trip_graph()
    assert graph.checkpointer is not None


def test_memory_checkpointer_is_the_default_backend():
    # Ships DEAD-safe: default backend is the in-process MemorySaver (no Postgres).
    from langgraph.checkpoint.memory import MemorySaver

    assert isinstance(_build_checkpointer(), MemorySaver)


def test_postgres_backend_is_guarded_not_implemented(monkeypatch):
    # The Postgres branch is deferred behind the flag (STOP point) — selecting it
    # must fail loudly, never silently mis-persist.
    from core.config import settings

    monkeypatch.setattr(settings, "trip_checkpointer_backend", "postgres")
    with pytest.raises(NotImplementedError):
        _build_checkpointer()


# --- plan_trip contract is unchanged (additive-only) ------------------------


@pytest.mark.asyncio
async def test_plan_trip_returns_additive_only_agentresponse_contract():
    # The reply is now the elicit/plan output (not a canned stub), but the additive
    # contract holds: empty onsens/hotels, recommendation None.
    with patch.object(slots_module, "_llm", _mock_llm()):
        resp = await plan_trip("plan a 5-night onsen trip", session_id=str(uuid.uuid4()))
    assert isinstance(resp.reply, str) and resp.reply
    assert resp.onsens == []
    assert resp.hotels == []
    assert resp.recommendation is None


@pytest.mark.asyncio
async def test_plan_trip_flows_through_checkpointed_graph_across_turns():
    # End-to-end via plan_trip (not the raw graph): two turns on one session_id must
    # accumulate in the module-level singleton's MemorySaver.
    session_id = str(uuid.uuid4())
    with patch.object(slots_module, "_llm", _mock_llm()):
        await plan_trip("turn one", session_id)
        await plan_trip("turn two", session_id)

    from agent.trip.graph import trip_graph

    snapshot = trip_graph.get_state(_cfg(session_id))
    assert snapshot.values["turn_count"] == 2
