"""Unit tests for agent.workflow.intent.

`parse_intent` builds a message list (SystemMessage + history + new HumanMessage)
and calls a module-level structured-output LLM (`_llm`) whose `ainvoke` returns a
validated `Intent`. We patch that module-level `_llm` so no real OpenAI call is
made: `patch.object(intent, "_llm")` with a MagicMock whose `ainvoke` is an
AsyncMock returning a canned Intent. Tests assert the CONTRACT — the returned
object is exactly what the LLM produced, and the message list passed to the LLM
has the right shape/order — not the system-prompt wording.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.workflow import intent as intent_module
from agent.workflow.intent import Intent, parse_intent


def _mock_llm(return_intent: Intent) -> MagicMock:
    """A stand-in for the module-level structured-output `_llm`.

    Only `ainvoke` is exercised by `parse_intent`; it must be awaitable and
    return the canned `Intent` instance.
    """
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=return_intent)
    return llm


@pytest.mark.asyncio
async def test_parse_intent_returns_structured_intent_from_llm():
    # Arrange — the LLM produces a fully-populated Intent.
    canned = Intent(prefecture="Shizuoka", query="ocean view onsen", wants_hotels=False)
    with patch.object(intent_module, "_llm", _mock_llm(canned)):
        # Act
        result = await parse_intent("onsen with ocean views in Shizuoka", [])
    # Assert — same object, right type and field values.
    assert isinstance(result, Intent)
    assert result is canned
    assert result.prefecture == "Shizuoka"
    assert result.query == "ocean view onsen"
    assert result.wants_hotels is False


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


@pytest.mark.asyncio
async def test_parse_intent_wants_hotels_true_flows_through():
    # Arrange — the user asked about lodging.
    canned = Intent(prefecture="Oita", query="sulfur onsen", wants_hotels=True)
    with patch.object(intent_module, "_llm", _mock_llm(canned)):
        # Act
        result = await parse_intent("onsen ryokan to stay at in Beppu", [])
    # Assert
    assert result.wants_hotels is True
    assert result.prefecture == "Oita"


@pytest.mark.asyncio
async def test_parse_intent_prefecture_none_flows_through():
    # Arrange — the user named no location, so prefecture is None.
    canned = Intent(prefecture=None, query="relaxing rotenburo", wants_hotels=False)
    with patch.object(intent_module, "_llm", _mock_llm(canned)):
        # Act
        result = await parse_intent("a relaxing outdoor bath", [])
    # Assert
    assert result.prefecture is None
    assert result.query == "relaxing rotenburo"


def test_intent_model_defaults():
    # Arrange / Act — only the required `query` field is supplied.
    model = Intent(query="x")
    # Assert — optional fields fall back to their declared defaults.
    assert model.prefecture is None
    assert model.wants_hotels is False
    assert model.query == "x"
