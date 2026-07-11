"""PR3a LangGraph skeleton tests — the checkpointer persistence proof.

The whole point of PR3a is that the trip-planner now flows through a hand-built,
checkpointed ``StateGraph`` (not ``create_react_agent``), and that working state
persists+accumulates across two turns of the SAME ``session_id`` (thread_id). These
tests pin exactly that, plus the four re-planning-readiness properties from
docs/v3-trip-planner-plan.md §0 that later slices depend on.

Each test builds a FRESH graph via ``build_trip_graph`` so the in-process
MemorySaver starts empty and turn counts are deterministic (the module-level
``trip_graph`` singleton is shared/long-lived and would carry state between tests).
"""

import uuid

import pytest

from agent.trip.agent import _TRIP_STUB_REPLY, plan_trip
from agent.trip.graph import (
    TRIP_STUB_REPLY,
    _build_checkpointer,
    build_trip_graph,
)


def _cfg(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


# --- the core Done condition: two-turn state persistence --------------------


@pytest.mark.asyncio
async def test_state_persists_and_accumulates_across_two_turns_same_session():
    # Fresh graph → empty MemorySaver, so turn_count is deterministic.
    graph = build_trip_graph()
    session_id = "trip-session-A"

    # Turn 1: no prior checkpoint → node sees turn_count absent, returns 1.
    r1 = await graph.ainvoke({"message": "5 nights in Gifu"}, config=_cfg(session_id))
    assert r1["turn_count"] == 1
    assert r1["reply"] == TRIP_STUB_REPLY

    # Turn 2, SAME thread_id: the node must OBSERVE the accumulated state left by
    # turn 1 (turn_count==1) and increment to 2. This is the checkpointer proof.
    r2 = await graph.ainvoke({"message": "make it mountain views"}, config=_cfg(session_id))
    assert r2["turn_count"] == 2

    # And the checkpointer actually holds it, keyed by thread_id.
    snapshot = graph.get_state(_cfg(session_id))
    assert snapshot.values["turn_count"] == 2


@pytest.mark.asyncio
async def test_state_is_isolated_per_session_id():
    # Different thread_ids must NOT share accumulated state.
    graph = build_trip_graph()

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
async def test_plan_trip_returns_unchanged_agentresponse_contract():
    # Unique session so the shared module-level trip_graph singleton stays clean.
    resp = await plan_trip("plan a 5-night onsen trip", session_id=str(uuid.uuid4()))
    assert resp.reply == _TRIP_STUB_REPLY
    assert resp.onsens == []
    assert resp.hotels == []
    assert resp.recommendation is None


@pytest.mark.asyncio
async def test_plan_trip_flows_through_checkpointed_graph_across_turns():
    # End-to-end via plan_trip (not the raw graph): two turns on one session_id must
    # accumulate in the module-level singleton's MemorySaver.
    session_id = str(uuid.uuid4())
    await plan_trip("turn one", session_id)
    await plan_trip("turn two", session_id)

    from agent.trip.graph import trip_graph

    snapshot = trip_graph.get_state(_cfg(session_id))
    assert snapshot.values["turn_count"] == 2
