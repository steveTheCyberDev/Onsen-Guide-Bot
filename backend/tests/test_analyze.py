"""Unit tests for the RECOMMEND brain (agent.workflow.analyze.analyze_onsen).

The module-level structured-output `_llm` is mocked so no real OpenAI call
happens: its `ainvoke` returns a canned `GuideResult`. Tests assert that the
per-candidate pros/cons merge back onto the correct OnsenResult BY INDEX, that
the recommendation flows through, that the compact projection sent to the model
excludes coordinates and URLs, and that out-of-range / no-candidate cases are
handled without fabricating fields beyond what the LLM returned.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.agent import OnsenResult
from agent.workflow import analyze as analyze_module
from agent.workflow.analyze import GuideResult, _OnsenAnalysis, _project, analyze_onsen


def _onsen(name, **overrides):
    base = dict(
        name=name,
        location="Beppu",
        spring_type="Sulfur",
        spa_quality="A relaxing sulfur spring with mountain views.",
        lat=33.2846,
        lng=131.4914,
    )
    base.update(overrides)
    return OnsenResult(**base)


def _mock_llm(result: GuideResult) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=result)
    return llm


@pytest.mark.asyncio
async def test_pros_cons_merge_back_by_index():
    # Arrange — two candidates; analyses intentionally OUT OF ORDER to prove the
    # merge is keyed by index, not by position.
    onsens = [_onsen("Alpha"), _onsen("Bravo")]
    guide = GuideResult(
        analyses=[
            _OnsenAnalysis(index=1, pros=["b-pro"], cons=["b-con"]),
            _OnsenAnalysis(index=0, pros=["a-pro"], cons=["a-con"]),
        ],
        recommendation="Go with Alpha.",
    )
    # Act
    with patch.object(analyze_module, "_llm", _mock_llm(guide)):
        result_onsens, recommendation = await analyze_onsen("relaxing", onsens)
    # Assert
    assert result_onsens[0].pros == ["a-pro"] and result_onsens[0].cons == ["a-con"]
    assert result_onsens[1].pros == ["b-pro"] and result_onsens[1].cons == ["b-con"]
    assert recommendation == "Go with Alpha."


@pytest.mark.asyncio
async def test_out_of_range_index_is_dropped_without_crashing():
    # Arrange — one real candidate but an analysis pointing at index 5.
    onsens = [_onsen("Solo")]
    guide = GuideResult(
        analyses=[_OnsenAnalysis(index=5, pros=["ghost"], cons=[])],
        recommendation="N/A",
    )
    # Act
    with patch.object(analyze_module, "_llm", _mock_llm(guide)):
        result_onsens, _ = await analyze_onsen("relaxing", onsens)
    # Assert — the bogus index is ignored; the real onsen keeps empty pros/cons.
    assert result_onsens[0].pros == []
    assert result_onsens[0].cons == []


@pytest.mark.asyncio
async def test_does_not_fabricate_fields_beyond_llm_output():
    # Arrange — the LLM returns NO analyses (grounding produced nothing usable).
    onsens = [_onsen("Quiet")]
    guide = GuideResult(analyses=[], recommendation="Not much to differentiate.")
    # Act
    with patch.object(analyze_module, "_llm", _mock_llm(guide)):
        result_onsens, recommendation = await analyze_onsen("relaxing", onsens)
    # Assert — pros/cons stay empty (nothing invented); recommendation is verbatim.
    assert result_onsens[0].pros == []
    assert result_onsens[0].cons == []
    assert recommendation == "Not much to differentiate."


@pytest.mark.asyncio
async def test_no_candidates_skips_llm_and_returns_none_recommendation():
    # Arrange — empty candidate list must short-circuit (no LLM call).
    llm = _mock_llm(GuideResult(analyses=[], recommendation="should not be used"))
    # Act
    with patch.object(analyze_module, "_llm", llm):
        result_onsens, recommendation = await analyze_onsen("relaxing", [])
    # Assert
    assert result_onsens == []
    assert recommendation is None
    llm.ainvoke.assert_not_awaited()


# --- compact projection ----------------------------------------------------


def test_projection_excludes_coords_and_urls():
    # Arrange
    onsens = [_onsen("Coastal", lat=12.345678, lng=98.7654321)]
    # Act
    projection = _project(onsens)
    # Assert — coordinates never appear in the prompt projection.
    assert "12.345678" not in projection
    assert "98.7654321" not in projection
    assert "lat" not in projection.lower()
    assert "http" not in projection.lower()


def test_projection_includes_judgement_fields():
    # Arrange
    onsens = [_onsen("Coastal")]
    # Act
    projection = _project(onsens)
    # Assert — name, spring type, location, and description ARE sent.
    assert "Coastal" in projection
    assert "Sulfur" in projection
    assert "Beppu" in projection
    assert "Description:" in projection


def test_projection_truncates_long_description():
    # Arrange — description longer than the cap.
    long_desc = "x" * 500
    onsens = [_onsen("Wordy", spa_quality=long_desc)]
    # Act
    projection = _project(onsens)
    # Assert — truncated to the cap plus an ellipsis, not the full 500 chars.
    assert "x" * 500 not in projection
    assert "…" in projection
