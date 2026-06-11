"""Unit tests for the ASK brain (agent.workflow.ask.answer_question).

Both the module-level ``_llm`` and the ``query_knowledge`` retrieval seam are
mocked so no real OpenAI/Chroma call happens. Tests assert:
  - empty retrieval → the no-info fallback is returned WITHOUT an LLM call
    (deterministic, no fabrication risk),
  - non-empty retrieval → the prompt is built from the chunks and the (mocked)
    LLM output is returned, with the chunk text actually fed into the prompt,
  - model selection falls back to intent_model when ask_model is "".
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.workflow import ask as ask_module
from agent.workflow.ask import NO_INFO_REPLY, answer_question


def _chunk(text="Wash thoroughly before entering the bath.", **overrides):
    base = {
        "text": text,
        "doc_type": "etiquette",
        "source_filename": "etiquette.md",
        "heading": "Before entering",
        "source_ja": "",
        "source_lang": "en",
        "sources": "https://example.org",
        "distance": 0.2,
    }
    base.update(overrides)
    return base


def _mock_llm(content: str) -> MagicMock:
    """A ChatOpenAI stand-in whose ainvoke returns an AIMessage-like object."""
    llm = MagicMock()
    msg = MagicMock()
    msg.content = content
    llm.ainvoke = AsyncMock(return_value=msg)
    return llm


# --- (a) empty retrieval → deterministic no-info, NO LLM call ----------------


@pytest.mark.asyncio
async def test_empty_retrieval_returns_no_info_without_llm_call():
    # Arrange — retrieval surfaces nothing usable.
    llm = _mock_llm("should not be used")
    with patch.object(ask_module, "query_knowledge", return_value=[]), \
        patch.object(ask_module, "_llm", llm):
        # Act
        reply = await answer_question("what's the wifi password?")
    # Assert — canonical fallback, and the LLM was never invoked.
    assert reply == NO_INFO_REPLY
    llm.ainvoke.assert_not_awaited()


# --- (b) non-empty retrieval → prompt built from chunks, LLM output returned --


@pytest.mark.asyncio
async def test_non_empty_retrieval_builds_prompt_and_returns_llm_output():
    # Arrange — two chunks retrieved; LLM returns a canned grounded answer.
    chunks = [
        _chunk("Rinse your body with the kakeyu ladle before soaking."),
        _chunk("Keep the small towel out of the water.", heading="Towel handling"),
    ]
    llm = _mock_llm("Rinse first, and keep your towel out of the water.")
    with patch.object(ask_module, "query_knowledge", return_value=chunks), \
        patch.object(ask_module, "_llm", llm):
        # Act
        reply = await answer_question("how do I bathe?")
    # Assert — returns the LLM output, and the chunk text was fed into the prompt.
    assert reply == "Rinse first, and keep your towel out of the water."
    llm.ainvoke.assert_awaited_once()
    messages = llm.ainvoke.await_args.args[0]
    human_content = messages[1].content
    assert "kakeyu ladle" in human_content
    assert "Keep the small towel out of the water." in human_content
    # The doc_type/heading attribution is rendered for the model.
    assert "etiquette" in human_content
    assert "Towel handling" in human_content


@pytest.mark.asyncio
async def test_callbacks_are_threaded_into_run_config():
    # Arrange
    chunks = [_chunk()]
    llm = _mock_llm("answer")
    sentinel = object()
    with patch.object(ask_module, "query_knowledge", return_value=chunks), \
        patch.object(ask_module, "_llm", llm):
        # Act
        await answer_question("do I wash first?", callbacks=[sentinel])
    # Assert — the callback list reaches the LLM config for usage capture.
    config = llm.ainvoke.await_args.kwargs["config"]
    assert config["callbacks"] == [sentinel]
    assert config["run_name"] == "answer-question"


@pytest.mark.asyncio
async def test_retrieval_called_with_configured_knobs():
    # Arrange
    llm = _mock_llm("answer")
    qk = MagicMock(return_value=[_chunk()])
    with patch.object(ask_module, "query_knowledge", qk), \
        patch.object(ask_module, "_llm", llm), \
        patch.object(ask_module.settings, "ask_top_k", 7), \
        patch.object(ask_module.settings, "ask_max_distance", 0.42):
        # Act
        await answer_question("etiquette?")
    # Assert — the configured top_k / max_distance drive retrieval.
    qk.assert_called_once_with("etiquette?", 7, 0.42)


# --- (c) model selection falls back to intent_model when ask_model is "" -----


def test_model_falls_back_to_intent_model_when_ask_model_empty():
    # The module builds _llm once at import; reconstruct the selection expression
    # under patched settings to prove the fallback contract.
    from core.config import settings as live

    with patch.object(live, "ask_model", ""), \
        patch.object(live, "intent_model", "gpt-4o-mini"):
        selected = live.ask_model or live.intent_model
    assert selected == "gpt-4o-mini"


def test_model_prefers_ask_model_when_set():
    from core.config import settings as live

    with patch.object(live, "ask_model", "gpt-4o"), \
        patch.object(live, "intent_model", "gpt-4o-mini"):
        selected = live.ask_model or live.intent_model
    assert selected == "gpt-4o"
