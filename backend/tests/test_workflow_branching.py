"""Mode-branching tests for agent.workflow.pipeline.run_workflow (V2.5).

Covers how run_workflow routes on Intent.mode and the analyze_enabled gate:
  - search   → onsens populated, recommendation None, pros/cons untouched,
               analyze_onsen NOT called.
  - recommend + analyze_enabled=True  → analyze_onsen called; its pros/cons +
               recommendation attach to the response.
  - recommend + analyze_enabled=False → analyze_onsen NOT called; bare candidates.
  - ask      → stub reply, NO retrieval, NO analyze.

Every dependency is mocked at the pipeline module namespace (pipeline.py imports
each name into its own module). analyze_onsen returns the (onsens, recommendation)
tuple its real signature does.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.agent import OnsenResult
from agent.workflow import pipeline
from agent.workflow.intent import Intent


def _record(name="Beppu Onsen", spa_quality="Sulfur spring", **extra):
    rec = {
        "name": name,
        "location": "Beppu",
        "spring_type": "Sulfur",
        "spa_quality": spa_quality,
        "lat": 33.2846,
        "lng": 131.4914,
    }
    rec.update(extra)
    return rec


def _patches(intent, records, analyze_return=None):
    """Patch pipeline deps. analyze_onsen returns (onsens, recommendation)."""
    analyze_mock = AsyncMock(return_value=analyze_return)
    cms = {
        "parse_intent": patch.object(pipeline, "parse_intent", new=AsyncMock(return_value=intent)),
        "query_onsen_structured": patch.object(pipeline, "query_onsen_structured", return_value=records),
        "analyze_onsen": patch.object(pipeline, "analyze_onsen", new=analyze_mock),
        "search_hotels": patch.object(pipeline, "search_hotels", return_value=[]),
        "get_history": patch.object(pipeline, "get_history", return_value=[]),
        "save_message": patch.object(pipeline, "save_message"),
    }
    return cms, analyze_mock


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


# --- search mode -----------------------------------------------------------


@pytest.mark.asyncio
async def test_search_mode_populates_onsens_and_skips_analyze():
    # Arrange
    intent = Intent(mode="search", prefecture="Oita", query="sulfur", wants_hotels=False)
    cms, analyze_mock = _patches(intent, [_record()])
    # Act
    with _Enter(cms):
        result = await pipeline.run_workflow("onsen in Oita", "s1")
    # Assert
    assert len(result["onsens"]) == 1
    assert result["recommendation"] is None
    assert result["onsens"][0]["pros"] == [] and result["onsens"][0]["cons"] == []
    analyze_mock.assert_not_awaited()


# --- recommend mode, analyze ON --------------------------------------------


@pytest.mark.asyncio
async def test_recommend_mode_with_analyze_enabled_calls_analyze_and_attaches():
    # Arrange — analyze returns enriched onsens (pros/cons) + a recommendation.
    intent = Intent(mode="recommend", prefecture="Oita", query="relaxing", wants_hotels=False)
    enriched = [
        OnsenResult(
            name="Beppu Onsen",
            location="Beppu",
            spring_type="Sulfur",
            spa_quality="Sulfur spring",
            lat=33.2846,
            lng=131.4914,
            pros=["scenic", "historic"],
            cons=["crowded"],
        )
    ]
    cms, analyze_mock = _patches(intent, [_record()], analyze_return=(enriched, "Pick Beppu Onsen."))
    # Act
    with patch.object(pipeline.settings, "analyze_enabled", True):
        with _Enter(cms):
            result = await pipeline.run_workflow("a relaxing onsen", "s1")
    # Assert
    analyze_mock.assert_awaited_once()
    assert result["recommendation"] == "Pick Beppu Onsen."
    assert result["onsens"][0]["pros"] == ["scenic", "historic"]
    assert result["onsens"][0]["cons"] == ["crowded"]


# --- recommend mode, analyze OFF -------------------------------------------


@pytest.mark.asyncio
async def test_recommend_mode_with_analyze_disabled_returns_bare_candidates():
    # Arrange
    intent = Intent(mode="recommend", prefecture="Oita", query="relaxing", wants_hotels=False)
    cms, analyze_mock = _patches(intent, [_record()])
    # Act — gate OFF: candidates returned without pros/cons or recommendation.
    with patch.object(pipeline.settings, "analyze_enabled", False):
        with _Enter(cms):
            result = await pipeline.run_workflow("a relaxing onsen", "s1")
    # Assert
    analyze_mock.assert_not_awaited()
    assert len(result["onsens"]) == 1
    assert result["recommendation"] is None
    assert result["onsens"][0]["pros"] == [] and result["onsens"][0]["cons"] == []


# --- ask mode --------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_mode_returns_stub_and_skips_retrieval_and_analyze():
    # Arrange
    intent = Intent(mode="ask", prefecture=None, query="tattoo policy", wants_hotels=False)
    cms, analyze_mock = _patches(intent, [_record()])
    # Act
    with _Enter(cms) as mocks:
        result = await pipeline.run_workflow("do onsen allow tattoos?", "s1")
    # Assert — stub reply, no result set, neither retrieval nor analyze run.
    assert result["reply"] == pipeline._ASK_STUB_REPLY
    assert result["onsens"] == []
    assert result["hotels"] == []
    assert result["recommendation"] is None
    mocks["query_onsen_structured"].assert_not_called()
    analyze_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ask_mode_still_persists_the_turn():
    # Arrange — ask mode should still save the conversation turn.
    intent = Intent(mode="ask", prefecture=None, query="bring", wants_hotels=False)
    cms, _ = _patches(intent, [])
    # Act
    with _Enter(cms) as mocks:
        await pipeline.run_workflow("what should I bring?", "s9")
    # Assert
    mocks["save_message"].assert_called_once()
    assert mocks["save_message"].call_args.args[0] == "s9"
