"""Tests for the per-request cost/mode attachment to the LangSmith run tree.

``agent.workflow.pipeline._attach_cost_to_trace(mode, summary)`` mutates the
CURRENT langsmith run tree (returned by ``get_current_run_tree``) so cost can be
sliced by mode (search|recommend|ask) in the LangSmith UI. It must be FULLY
fail-safe: when there is no active run (tracing disabled / langsmith absent),
``get_current_run_tree()`` returns None and the call is an inert no-op.

``get_current_run_tree`` is patched AT THE PIPELINE MODULE'S NAMESPACE
(``patch.object(pipeline, ...)``) because pipeline.py imports the name into its
own module, so patching langsmith's source would not affect the bound reference.
"""

from unittest.mock import patch

from agent.workflow import pipeline


class _FakeRunTree:
    """Stand-in for a langsmith RunTree exposing the attributes we mutate."""

    def __init__(self, tags=None):
        self.metadata: dict = {}
        self.tags: list[str] | None = tags


def _summary(
    models=None,
    input_tokens=2000,
    output_tokens=1000,
    cost_usd=0.0009,
) -> dict:
    """A summarize_usage()-shaped dict."""
    return {
        "models": models if models is not None else ["gpt-4o-mini"],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }


# --- happy path: fields land on metadata + tag added -----------------------


def test_attach_populates_metadata_and_tag():
    # Arrange — an active run tree with one pre-existing tag.
    rt = _FakeRunTree(tags=["chat"])
    summary = _summary(models=["gpt-4o", "gpt-4o-mini"], cost_usd=0.0084)
    # Act
    with patch.object(pipeline, "get_current_run_tree", return_value=rt):
        pipeline._attach_cost_to_trace("recommend", summary)
    # Assert — per-request fields attached to metadata.
    assert rt.metadata["mode"] == "recommend"
    assert rt.metadata["cost_usd"] == 0.0084
    assert rt.metadata["input_tokens"] == 2000
    assert rt.metadata["output_tokens"] == 1000
    assert rt.metadata["models"] == "gpt-4o,gpt-4o-mini"  # comma-joined
    # Assert — mode tag appended without dropping existing tags.
    assert "mode:recommend" in rt.tags
    assert "chat" in rt.tags


def test_attach_uses_none_when_no_models():
    # Arrange — empty models list (e.g. a request with no LLM call).
    rt = _FakeRunTree()
    # Act
    with patch.object(pipeline, "get_current_run_tree", return_value=rt):
        pipeline._attach_cost_to_trace("search", _summary(models=[]))
    # Assert — models string falls back to the "none" sentinel.
    assert rt.metadata["models"] == "none"
    assert rt.metadata["mode"] == "search"


def test_attach_initializes_tags_when_none():
    # Arrange — a run tree whose tags attr is None (must not crash on +).
    rt = _FakeRunTree(tags=None)
    # Act
    with patch.object(pipeline, "get_current_run_tree", return_value=rt):
        pipeline._attach_cost_to_trace("ask", _summary())
    # Assert
    assert rt.tags == ["mode:ask"]


# --- no-op path: no active run ---------------------------------------------


def test_attach_noop_when_no_active_run():
    # Arrange / Act — get_current_run_tree returns None (tracing disabled).
    with patch.object(pipeline, "get_current_run_tree", return_value=None):
        # Assert — must not raise.
        pipeline._attach_cost_to_trace("search", _summary())


def test_attach_swallows_unexpected_errors():
    # Arrange — get_current_run_tree itself blows up (defensive guard).
    with patch.object(
        pipeline, "get_current_run_tree", side_effect=RuntimeError("boom")
    ):
        # Assert — the error is swallowed; the request path is never disturbed.
        pipeline._attach_cost_to_trace("search", _summary())
