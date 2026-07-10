"""Trip-mode branching tests for run_workflow (V3 PR2 — dead stub).

The trip branch is deliberately asymmetric with the ask branch: it is taken ONLY
when `trip_enabled` is ON. With the gate OFF (prod default) a trip-classified
query must FALL THROUGH to the normal retrieval path (graceful degradation), not
dead-end on the stub. These tests pin both halves:

  - trip_enabled=True  → plan_trip stub reply, empty onsens/hotels, no retrieval,
                         cost logged + turn persisted.
  - trip_enabled=False → falls through: query_onsen_structured IS called, onsens
                         populated, reply is NOT the trip stub.

Dependencies are patched at the pipeline module namespace, mirroring
test_workflow_branching.py. plan_trip runs for real in the gate-ON test (the stub
makes no service/LLM calls), so we assert against its real reply.
"""

from unittest.mock import AsyncMock, patch

import pytest

from agent.trip.agent import _TRIP_STUB_REPLY
from agent.workflow import pipeline
from agent.workflow.intent import Intent


def _record(name="Beppu Onsen"):
    return {
        "name": name,
        "location": "Beppu",
        "spring_type": "Sulfur",
        "spa_quality": "Sulfur spring",
        "lat": 33.2846,
        "lng": 131.4914,
    }


def _patches(intent, records):
    """Patch pipeline deps at the module namespace (mirrors test_workflow_branching)."""
    return {
        "parse_intent": patch.object(
            pipeline, "parse_intent", new=AsyncMock(return_value=intent)
        ),
        "query_onsen_structured": patch.object(
            pipeline, "query_onsen_structured", return_value=records
        ),
        "analyze_onsen": patch.object(pipeline, "analyze_onsen", new=AsyncMock()),
        "answer_question": patch.object(pipeline, "answer_question", new=AsyncMock()),
        "search_hotels": patch.object(pipeline, "search_hotels", return_value=[]),
        "get_history": patch.object(pipeline, "get_history", return_value=[]),
        "save_message": patch.object(pipeline, "save_message"),
    }


class _Enter:
    def __init__(self, cms):
        self._cms = cms
        self.mocks = {}

    def __enter__(self):
        for k, cm in self._cms.items():
            self.mocks[k] = cm.__enter__()
        return self.mocks

    def __exit__(self, *exc):
        for cm in self._cms.values():
            cm.__exit__(*exc)
        return False


# --- trip mode, gate ON -----------------------------------------------------


@pytest.mark.asyncio
async def test_trip_mode_gate_on_returns_stub_and_skips_retrieval():
    # Arrange — trip_enabled ON: the plan_trip stub answers; no retrieval/analyze.
    intent = Intent(mode="trip", prefecture="Gifu", query="onsen trip", wants_hotels=False)
    cms = _patches(intent, [_record()])
    # Act
    with patch.object(pipeline.settings, "trip_enabled", True):
        with _Enter(cms) as mocks:
            result = await pipeline.run_workflow("plan a 3-day onsen trip in Gifu", "s1")
    # Assert — canned stub reply, empty result set, retrieval + analyze skipped.
    assert result["reply"] == _TRIP_STUB_REPLY
    assert result["onsens"] == []
    assert result["hotels"] == []
    assert result["recommendation"] is None
    mocks["query_onsen_structured"].assert_not_called()
    mocks["analyze_onsen"].assert_not_awaited()


@pytest.mark.asyncio
async def test_trip_mode_gate_on_logs_cost_and_persists_turn():
    # Arrange — even the stub must log cost once and persist the turn.
    intent = Intent(mode="trip", prefecture=None, query="onsen trip", wants_hotels=False)
    cms = _patches(intent, [])
    # Act
    with patch.object(pipeline.settings, "trip_enabled", True):
        with patch.object(pipeline, "_log_cost") as log_cost:
            with _Enter(cms) as mocks:
                await pipeline.run_workflow("plan a 5-night onsen trip", "s9")
    # Assert
    log_cost.assert_called_once()
    mocks["save_message"].assert_called_once()
    assert mocks["save_message"].call_args.args[0] == "s9"
    # The persisted AI turn is the stub reply.
    assert mocks["save_message"].call_args.args[2] == _TRIP_STUB_REPLY


# --- trip mode, gate OFF (prod default) — must FALL THROUGH ------------------


@pytest.mark.asyncio
async def test_trip_mode_gate_off_falls_through_to_retrieval():
    # Arrange — trip_enabled OFF (default): a trip query degrades to a normal
    # onsen search rather than dead-ending on the stub.
    intent = Intent(mode="trip", prefecture="Gifu", query="onsen trip", wants_hotels=False)
    cms = _patches(intent, [_record()])
    # Act
    with patch.object(pipeline.settings, "trip_enabled", False):
        with _Enter(cms) as mocks:
            result = await pipeline.run_workflow("plan a 3-day onsen trip in Gifu", "s1")
    # Assert — retrieval ran, onsens populated, and the reply is the SEARCH
    # template, NOT the trip stub.
    mocks["query_onsen_structured"].assert_called_once()
    assert len(result["onsens"]) == 1
    assert result["reply"] != _TRIP_STUB_REPLY
    assert result["recommendation"] is None
