"""Unit tests for agent.workflow.intent — the router (V2.5 modes + message shape).

`parse_intent` returns an `Intent` whose `mode` field routes the request
(search | recommend | ask | trip). The structured-output LLM is mocked at the module
namespace so no real OpenAI call happens; we assert that the mode the LLM
produces (and the other routing fields) flow through unchanged, that the
schema defaults are correct, and that the message list handed to the LLM has the
right shape/order — not the system-prompt wording.

(Absorbed the former ``test_intent.py``: its pass-through and model-default tests
were supersets-of/superseded-by the ones here, so only the message-ordering test
survived the merge.)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

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
        (
            "plan a 3-day onsen trip in Gifu and Nagano",
            Intent(
                mode="trip",
                prefecture="Gifu",
                query="onsen trip mountain",
                wants_hotels=False,
            ),
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


def test_intent_accepts_trip_mode():
    # Arrange / Act — the V3 seam: "trip" is a valid 4th mode value.
    intent = Intent(mode="trip", query="3-day onsen trip in Gifu and Nagano")
    # Assert
    assert intent.mode == "trip"


# --- message list handed to the LLM (shape + order) --------------------------


@pytest.mark.asyncio
async def test_parse_intent_passes_history_and_human_message_in_order():
    # Arrange — non-empty history must be threaded through, framed by a leading
    # SystemMessage and a trailing HumanMessage carrying the new user message.
    history = [
        HumanMessage(content="any onsen in Hakone?"),
        AIMessage(content="Here are a few options in Hakone."),
    ]
    canned = Intent(prefecture="Kanagawa", query="quiet onsen", wants_hotels=False)
    mock_llm = _mock_llm(canned)
    with patch.object(intent_module, "_llm", mock_llm):
        # Act
        await parse_intent("somewhere quieter", history)
    # Assert — inspect the message list handed to the LLM.
    mock_llm.ainvoke.assert_awaited_once()
    sent_messages = mock_llm.ainvoke.await_args.args[0]

    # A SystemMessage is present, first.
    assert isinstance(sent_messages[0], SystemMessage)
    # Ordering: system, then history (verbatim), then the new human message.
    assert sent_messages[1:3] == history
    assert isinstance(sent_messages[-1], HumanMessage)
    assert sent_messages[-1].content == "somewhere quieter"
    # Exactly: 1 system + len(history) + 1 human.
    assert len(sent_messages) == 1 + len(history) + 1
