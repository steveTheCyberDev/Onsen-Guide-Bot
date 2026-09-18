"""PR3a LangGraph skeleton tests — checkpointer persistence + readiness properties.

Originally pinned PR3a's inert canned-reply behaviour. PR3b reshaped the graph
(``START → gather → {elicit | plan} → END``) so ``gather`` now runs a structured-
output extraction LLM every turn; the old "canned reply, no LLM call" assumptions no
longer hold. What SURVIVES unchanged from PR3a and is still pinned here:

  * the four re-planning-readiness properties (discrete ``plan`` node,
    compiled-with-checkpointer, MemorySaver default, Postgres guarded); and
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
from unittest.mock import patch

import pytest

from agent.trip import slots as slots_module
from agent.trip.agent import plan_trip
from agent.trip.graph import _build_checkpointer, build_trip_graph
from tests.conftest import trip_cfg, trip_slots_llm


# --- the core Done condition: two-turn state persistence --------------------
#
# The raw turn_count persistence/isolation proofs that used to live here are now
# covered — as strict supersets — by ``test_trip_slots_elicit_loop.py``:
#   * ``test_partial_then_complete_across_two_turns_same_session`` asserts the same
#     ``turn_count == 2`` PLUS that the slots themselves persisted and merged;
#   * ``test_slots_isolated_per_session_id`` asserts the same A=2 / B=1 split PLUS
#     per-session slot isolation.
# The checkpointer-SHAPE properties below are unique to this file and stay put.


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
    with patch.object(slots_module, "_llm", trip_slots_llm()):
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
    with patch.object(slots_module, "_llm", trip_slots_llm()):
        await plan_trip("turn one", session_id)
        await plan_trip("turn two", session_id)

    from agent.trip.graph import trip_graph

    snapshot = trip_graph.get_state(trip_cfg(session_id))
    assert snapshot.values["turn_count"] == 2
