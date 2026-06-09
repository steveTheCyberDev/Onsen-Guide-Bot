"""Unit tests for the V2.5 router mode classification (agent.workflow.intent).

`parse_intent` returns an `Intent` whose `mode` field routes the request
(search | recommend | ask). The structured-output LLM is mocked at the module
namespace so no real OpenAI call happens; we assert that the mode the LLM
produces (and the other routing fields) flow through unchanged, and that the
schema defaults are correct.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.workflow import intent as intent_module
from agent.workflow.intent import Intent, parse_intent


def _mock_llm(return_intent: Intent) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=return_intent)
    return llm


@pytest.mark.parametrize(
    "phrasing, canned",
    [
        (
            "find onsen in Okinawa",
            Intent(mode="search", prefecture="Okinawa", query="onsen", wants_hotels=False),
        ),
        (
            "a relaxing onsen with mountain views, somewhere good for families",
            Intent(
                mode="recommend",
                prefecture=None,
                query="relaxing mountain-view onsen for families",
                wants_hotels=False,
            ),
        ),
        (
            "do onsen allow tattoos? what should I bring?",
            Intent(mode="ask", prefecture=None, query="onsen etiquette tattoos", wants_hotels=False),
        ),
    ],
)
@pytest.mark.asyncio
async def test_parse_intent_returns_expected_mode(phrasing, canned):
    # Arrange — the (mocked) LLM classifies the phrasing into a mode.
    with patch.object(intent_module, "_llm", _mock_llm(canned)):
        # Act
        result = await parse_intent(phrasing, [])
    # Assert
    assert result.mode == canned.mode


@pytest.mark.asyncio
async def test_existing_fields_still_populate_alongside_mode():
    # Arrange — recommend mode must NOT clobber prefecture/query/wants_hotels.
    canned = Intent(
        mode="recommend",
        prefecture="Gunma",
        query="best outdoor baths",
        wants_hotels=True,
    )
    with patch.object(intent_module, "_llm", _mock_llm(canned)):
        # Act
        result = await parse_intent("best outdoor baths in Gunma with a hotel", [])
    # Assert — every field flows through verbatim from the LLM result.
    assert result.mode == "recommend"
    assert result.prefecture == "Gunma"
    assert result.query == "best outdoor baths"
    assert result.wants_hotels is True


def test_intent_mode_defaults_to_search():
    # Arrange / Act — mode is optional; default keeps legacy search behaviour.
    intent = Intent(query="onsen")
    # Assert
    assert intent.mode == "search"
    assert intent.prefecture is None
    assert intent.wants_hotels is False
