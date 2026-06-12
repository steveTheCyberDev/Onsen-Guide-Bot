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


def _diag(min_distance=0.2, retrieved=1, kept=1) -> dict:
    """A query_knowledge_with_diagnostics()-shaped diagnostics dict."""
    return {"min_distance": min_distance, "retrieved": retrieved, "kept": kept}


def _qkd(records, diagnostics=None) -> MagicMock:
    """Stand-in for query_knowledge_with_diagnostics → (records, diagnostics)."""
    diagnostics = diagnostics if diagnostics is not None else _diag(
        retrieved=len(records), kept=len(records)
    )
    return MagicMock(return_value=(records, diagnostics))


# --- (a) empty retrieval → deterministic no-info, NO LLM call ----------------


@pytest.mark.asyncio
async def test_empty_retrieval_returns_no_info_without_llm_call():
    # Arrange — retrieval surfaces nothing usable.
    llm = _mock_llm("should not be used")
    with patch.object(ask_module, "query_knowledge_with_diagnostics", _qkd([])), \
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
    with patch.object(ask_module, "query_knowledge_with_diagnostics", _qkd(chunks)), \
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
    with patch.object(ask_module, "query_knowledge_with_diagnostics", _qkd(chunks)), \
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
    qk = _qkd([_chunk()])
    with patch.object(ask_module, "query_knowledge_with_diagnostics", qk), \
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


# --- no-info instrumentation: both paths fire _instrument_no_info ------------


@pytest.mark.asyncio
async def test_empty_retrieval_path_instruments_with_empty_retrieval_tag():
    # Arrange — empty retrieval; capture the instrumentation call. Diagnostics
    # describe a TRUE coverage gap (nothing retrieved → min_distance None).
    llm = _mock_llm("should not be used")
    diag = _diag(min_distance=None, retrieved=0, kept=0)
    instrument = MagicMock()
    with patch.object(ask_module, "query_knowledge_with_diagnostics", _qkd([], diag)), \
        patch.object(ask_module, "_llm", llm), \
        patch.object(ask_module, "_instrument_no_info", instrument):
        # Act
        reply = await answer_question("what's the wifi password?")
    # Assert — no-info reply, LLM untouched, and instrumented as "empty_retrieval".
    assert reply == NO_INFO_REPLY
    llm.ainvoke.assert_not_awaited()
    instrument.assert_called_once_with(
        "empty_retrieval", "what's the wifi password?", diag
    )


@pytest.mark.asyncio
async def test_llm_refusal_path_instruments_with_llm_refusal_tag():
    # Arrange — chunks ARE retrieved (low min_distance) but the LLM returns the
    # canonical fallback verbatim → a FALSE refusal that must be instrumented.
    chunks = [_chunk()]
    diag = _diag(min_distance=0.18, retrieved=1, kept=1)
    llm = _mock_llm(NO_INFO_REPLY)
    instrument = MagicMock()
    with patch.object(ask_module, "query_knowledge_with_diagnostics", _qkd(chunks, diag)), \
        patch.object(ask_module, "_llm", llm), \
        patch.object(ask_module, "_instrument_no_info", instrument):
        # Act
        reply = await answer_question("how do I bathe?")
    # Assert — the LLM WAS invoked, reply is the fallback, instrumented as refusal.
    assert reply == NO_INFO_REPLY
    llm.ainvoke.assert_awaited_once()
    instrument.assert_called_once_with("llm_refusal", "how do I bathe?", diag)


@pytest.mark.asyncio
async def test_grounded_answer_does_not_instrument_no_info():
    # Arrange — a real grounded answer (not the fallback) must NOT be flagged.
    chunks = [_chunk()]
    llm = _mock_llm("Rinse first, then soak.")
    instrument = MagicMock()
    with patch.object(ask_module, "query_knowledge_with_diagnostics", _qkd(chunks)), \
        patch.object(ask_module, "_llm", llm), \
        patch.object(ask_module, "_instrument_no_info", instrument):
        # Act
        reply = await answer_question("how do I bathe?")
    # Assert
    assert reply == "Rinse first, then soak."
    instrument.assert_not_called()


# --- _instrument_no_info itself: logs + fail-safe run-tree tagging -----------


def test_instrument_no_info_emits_structured_log(caplog):
    # Arrange — no active run; the structured log line must still fire.
    import logging

    with patch.object(ask_module, "get_current_run_tree", return_value=None), \
        caplog.at_level(logging.INFO, logger=ask_module.logger.name):
        # Act
        ask_module._instrument_no_info(
            "empty_retrieval", "long question", _diag(min_distance=None, retrieved=0, kept=0)
        )
    # Assert — one queryable ask_no_info line carrying the path + diagnostics.
    assert any(
        "ask_no_info" in r.getMessage() and "empty_retrieval" in r.getMessage()
        for r in caplog.records
    )


def test_instrument_no_info_tags_active_run_tree():
    # Arrange — an active run tree should receive metadata + tags.
    class _FakeRunTree:
        def __init__(self):
            self.metadata: dict = {}
            self.tags: list[str] | None = ["chat"]

    rt = _FakeRunTree()
    with patch.object(ask_module, "get_current_run_tree", return_value=rt):
        # Act
        ask_module._instrument_no_info(
            "llm_refusal", "how do I bathe?", _diag(min_distance=0.18, retrieved=1, kept=1)
        )
    # Assert — metadata captures path + diagnostics; tags appended, not replaced.
    assert rt.metadata["ask_no_info"] is True
    assert rt.metadata["ask_no_info_path"] == "llm_refusal"
    assert rt.metadata["ask_min_distance"] == 0.18
    assert rt.metadata["ask_retrieved"] == 1
    assert rt.metadata["ask_kept"] == 1
    assert "ask_no_info" in rt.tags
    assert "ask_no_info_path:llm_refusal" in rt.tags
    assert "chat" in rt.tags


def test_instrument_no_info_noop_when_no_active_run():
    # Arrange / Act — no active run must not raise.
    with patch.object(ask_module, "get_current_run_tree", return_value=None):
        ask_module._instrument_no_info("empty_retrieval", "q", _diag())


def test_instrument_no_info_swallows_run_tree_errors():
    # Arrange — run-tree accessor blows up; instrumentation must never leak into
    # the request path (fail-closed on instrumentation errors).
    with patch.object(
        ask_module, "get_current_run_tree", side_effect=RuntimeError("boom")
    ):
        # Assert — no exception escapes.
        ask_module._instrument_no_info("llm_refusal", "q", _diag())
