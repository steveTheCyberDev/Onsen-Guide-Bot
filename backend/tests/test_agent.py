"""Unit tests for agent.agent.run_agent.

These tests pin the refactor that removed request-time geocoding of returned
onsen. Onsen coordinates now flow purely from the structured agent response
(which the LLM fills verbatim from the search_onsen tool output, whose text
carries lat/lng from ChromaDB metadata). `run_agent` must NOT call the geocoding
service for returned onsen.

The LangGraph `graph.ainvoke` is mocked so no LLM/network call is made; we only
exercise `run_agent`'s post-processing.
"""

from unittest.mock import AsyncMock, patch

import pytest

from agent import agent
from agent.agent import AgentResponse, OnsenResult


def _structured(onsens):
    return {"structured_response": AgentResponse(reply="ok", onsens=onsens, hotels=[])}


@pytest.mark.asyncio
async def test_run_agent_carries_onsen_coordinates_from_structured_response():
    # Arrange — the structured response already has coordinates (the LLM copied
    # them from the tool output); run_agent must surface them unchanged.
    onsens = [
        OnsenResult(
            name="Beppu Onsen",
            location="Beppu",
            spring_type="Sulfur",
            spa_quality="Sulfur spring",
            lat=33.2846,
            lng=131.4914,
        )
    ]
    with patch.object(agent.graph, "ainvoke", new=AsyncMock(return_value=_structured(onsens))), \
            patch.object(agent, "get_history", return_value=[]), \
            patch.object(agent, "save_message"):
        # Act
        result = await agent.run_agent("onsen in beppu", "s1")
    # Assert
    assert result["onsens"][0]["lat"] == 33.2846
    assert result["onsens"][0]["lng"] == 131.4914


@pytest.mark.asyncio
async def test_run_agent_does_not_geocode_returned_onsen():
    # Arrange — the geocoding service must not be invoked for returned onsen;
    # request-time geocoding of onsen was removed in favour of ingest-time coords.
    onsens = [
        OnsenResult(
            name="No Coords Onsen",
            location="Somewhere",
            spring_type="Simple",
            spa_quality="Simple spring",
        )
    ]
    # `geocode` is no longer imported into agent.agent at all; assert that and
    # also that the geocoding service is never touched during run_agent.
    assert not hasattr(agent, "geocode")
    with patch.object(agent.graph, "ainvoke", new=AsyncMock(return_value=_structured(onsens))), \
            patch.object(agent, "get_history", return_value=[]), \
            patch.object(agent, "save_message"), \
            patch("services.geocoding.geocoding_service.geocode") as mock_geocode:
        # Act
        result = await agent.run_agent("onsen somewhere", "s1")
    # Assert — coords stay null (tool gave none) and no geocode call was made
    assert result["onsens"][0]["lat"] is None
    assert result["onsens"][0]["lng"] is None
    mock_geocode.assert_not_called()


def test_geocode_location_tool_still_registered():
    # The geocode_location tool is still used in the hotel-search reasoning path
    # and must remain registered even though onsen geocoding was removed.
    from agent.tools.geocoding_tool import geocode_location

    assert geocode_location in agent.tools
