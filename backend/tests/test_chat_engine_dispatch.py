"""Unit tests for the chat_engine dispatcher in agent.agent.run_agent.

`run_agent` is the A/B + instant-rollback seam: it routes /chat to either the
legacy ReAct agent (run_react_agent) or the deterministic V2 workflow
(run_workflow), based on settings.chat_engine (env CHAT_ENGINE).

These tests pin the routing CONTRACT only — which engine coroutine is awaited,
with which args, and that its return value is propagated — without asserting on
log text. External engines are mocked (AsyncMock) so no LLM/network call is made.

run_workflow is imported lazily INSIDE run_agent (from agent.workflow.pipeline)
to avoid an import cycle, so it is patched at its definition site
`agent.workflow.pipeline.run_workflow`, not as a name on agent.agent.
"""

from unittest.mock import AsyncMock, patch

import pytest

from agent import agent
from core.config import Settings, settings


@pytest.mark.asyncio
async def test_run_agent_default_routes_to_react(monkeypatch):
    # Arrange — default engine is "react".
    monkeypatch.setattr(settings, "chat_engine", "react")
    sentinel = {"reply": "react-result", "onsens": [], "hotels": []}
    mock_react = AsyncMock(return_value=sentinel)

    with patch.object(agent, "run_react_agent", new=mock_react), \
            patch("agent.workflow.pipeline.run_workflow", new=AsyncMock()) as mock_workflow:
        # Act
        result = await agent.run_agent("onsen in beppu", "s1")

    # Assert — react engine awaited with (message, session_id); return propagated.
    mock_react.assert_awaited_once_with("onsen in beppu", "s1")
    mock_workflow.assert_not_called()
    assert result is sentinel


@pytest.mark.asyncio
async def test_run_agent_workflow_routes_to_run_workflow(monkeypatch):
    # Arrange — flip the seam to the deterministic workflow engine.
    monkeypatch.setattr(settings, "chat_engine", "workflow")
    sentinel = {"reply": "workflow-result", "onsens": [], "hotels": []}
    mock_workflow = AsyncMock(return_value=sentinel)
    mock_react = AsyncMock()

    # run_workflow is lazily imported from agent.workflow.pipeline INSIDE run_agent,
    # so patch it at the import target, not on agent.agent.
    with patch("agent.workflow.pipeline.run_workflow", new=mock_workflow), \
            patch.object(agent, "run_react_agent", new=mock_react):
        # Act
        result = await agent.run_agent("onsen in mie", "s2")

    # Assert — workflow awaited with (message, session_id); react NOT called.
    mock_workflow.assert_awaited_once_with("onsen in mie", "s2")
    mock_react.assert_not_called()
    assert result is sentinel


@pytest.mark.asyncio
async def test_run_agent_unknown_engine_falls_back_to_react(monkeypatch):
    # Arrange — any unrecognised value must fall back to the ReAct agent, never
    # the workflow (fail-safe to the current live behavior).
    monkeypatch.setattr(settings, "chat_engine", "banana")
    sentinel = {"reply": "react-fallback", "onsens": [], "hotels": []}
    mock_react = AsyncMock(return_value=sentinel)

    with patch.object(agent, "run_react_agent", new=mock_react), \
            patch("agent.workflow.pipeline.run_workflow", new=AsyncMock()) as mock_workflow:
        # Act
        result = await agent.run_agent("onsen somewhere", "s3")

    # Assert
    mock_react.assert_awaited_once_with("onsen somewhere", "s3")
    mock_workflow.assert_not_called()
    assert result is sentinel


def test_chat_engine_default_is_react():
    # A fresh Settings() (and the imported singleton) must default to "react" so
    # live behavior is unchanged until CHAT_ENGINE is explicitly flipped.
    assert Settings().chat_engine == "react"
    assert settings.chat_engine == "react"
