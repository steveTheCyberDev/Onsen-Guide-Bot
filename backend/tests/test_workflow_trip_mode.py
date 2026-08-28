"""Trip-mode branching tests for run_workflow (V3 PR2/PR3b).

The trip branch is deliberately asymmetric with the ask branch: it is taken ONLY
when `trip_enabled` is ON. With the gate OFF (prod default) a trip-classified
query must FALL THROUGH to the normal retrieval path (graceful degradation), not
dead-end. These tests pin both halves:

  - trip_enabled=True  → plan_trip runs the trip-planner graph (elicit/plan reply),
                         empty onsens/hotels, no retrieval, cost logged + turn
                         persisted.
  - trip_enabled=False → falls through: query_onsen_structured IS called, onsens
                         populated, reply comes from the search template.

Dependencies are patched at the pipeline module namespace, mirroring
test_workflow_branching.py. In the gate-ON tests plan_trip runs for real, so we mock
its ONE extraction LLM call (`agent.trip.slots._llm`) to keep the suite deterministic
and free — the reply is then the elicit follow-up (no slots extracted → missing
required).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.trip import slots as slots_module
from agent.trip.slots import SlotUpdate
from agent.workflow import pipeline
from agent.workflow.intent import Intent


def _mock_slots_llm() -> MagicMock:
    """Mock the trip-planner extraction LLM to return an empty delta (→ elicit)."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=SlotUpdate())
    return llm


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
async def test_trip_mode_gate_on_returns_trip_reply_and_skips_retrieval():
    # Arrange — trip_enabled ON: the trip-planner answers; no retrieval/analyze.
    intent = Intent(mode="trip", prefecture="Gifu", query="onsen trip", wants_hotels=False)
    cms = _patches(intent, [_record()])
    # Act
    with patch.object(pipeline.settings, "trip_enabled", True):
        with patch.object(slots_module, "_llm", _mock_slots_llm()):
            with _Enter(cms) as mocks:
                result = await pipeline.run_workflow(
                    "plan a 3-day onsen trip in Gifu", "s1"
                )
    # Assert — a non-empty trip-planner reply, empty result set, retrieval + analyze
    # skipped (the trip branch makes no services/ calls in this slice).
    assert isinstance(result["reply"], str) and result["reply"]
    assert result["onsens"] == []
    assert result["hotels"] == []
    assert result["recommendation"] is None
    mocks["query_onsen_structured"].assert_not_called()
    mocks["analyze_onsen"].assert_not_awaited()


@pytest.mark.asyncio
async def test_trip_mode_gate_on_logs_cost_and_persists_turn():
    # Arrange — the trip branch must log cost once and persist the turn's reply.
    intent = Intent(mode="trip", prefecture=None, query="onsen trip", wants_hotels=False)
    cms = _patches(intent, [])
    # Act
    with patch.object(pipeline.settings, "trip_enabled", True):
        with patch.object(slots_module, "_llm", _mock_slots_llm()):
            with patch.object(pipeline, "_log_cost") as log_cost:
                with _Enter(cms) as mocks:
                    result = await pipeline.run_workflow(
                        "plan a 5-night onsen trip", "s9"
                    )
    # Assert
    log_cost.assert_called_once()
    mocks["save_message"].assert_called_once()
    assert mocks["save_message"].call_args.args[0] == "s9"
    # The persisted AI turn is the trip-planner reply that was returned.
    assert mocks["save_message"].call_args.args[2] == result["reply"]


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
    # Assert — retrieval ran and onsens populated, i.e. the query fell through to the
    # normal SEARCH path instead of dead-ending on the (now gated-off) trip branch.
    mocks["query_onsen_structured"].assert_called_once()
    assert len(result["onsens"]) == 1
    assert isinstance(result["reply"], str) and result["reply"]
    assert result["recommendation"] is None
