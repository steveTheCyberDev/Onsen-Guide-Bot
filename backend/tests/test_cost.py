"""Pure-function tests for agent.workflow.cost.summarize_usage + pricing.

No mocking needed — these are deterministic calculations over a
usage_metadata dict (the shape UsageMetadataCallbackHandler produces: keyed by
model name, each value carrying input_tokens / output_tokens). Cost is in USD,
priced per 1,000 tokens, rounded to 6 dp.
"""

import pytest

from agent.workflow.cost import _PRICING_PER_1K, _price_for, summarize_usage


def _usage(input_tokens, output_tokens):
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


# --- single model ----------------------------------------------------------


def test_single_model_cost():
    # Arrange — gpt-4o: 0.0025 in / 0.010 out per 1k.
    usage = {"gpt-4o": _usage(1000, 500)}
    # Act
    summary = summarize_usage(usage)
    # Assert — 1000/1000*0.0025 + 500/1000*0.010 = 0.0025 + 0.005 = 0.0075
    assert summary["cost_usd"] == 0.0075
    assert summary["input_tokens"] == 1000
    assert summary["output_tokens"] == 500
    assert summary["models"] == ["gpt-4o"]


def test_single_model_mini_cost():
    # Arrange — gpt-4o-mini: 0.00015 in / 0.0006 out per 1k.
    usage = {"gpt-4o-mini": _usage(2000, 1000)}
    # Act
    summary = summarize_usage(usage)
    # Assert — 2*0.00015 + 1*0.0006 = 0.0003 + 0.0006 = 0.0009
    assert summary["cost_usd"] == 0.0009


# --- multiple models -------------------------------------------------------


def test_multiple_models_summed():
    # Arrange — intent (mini) + analyze (4o) in one request.
    usage = {
        "gpt-4o-mini": _usage(2000, 1000),  # 0.0009
        "gpt-4o": _usage(1000, 500),  # 0.0075
    }
    # Act
    summary = summarize_usage(usage)
    # Assert
    assert summary["cost_usd"] == round(0.0009 + 0.0075, 6)
    assert summary["input_tokens"] == 3000
    assert summary["output_tokens"] == 1500
    assert summary["models"] == ["gpt-4o", "gpt-4o-mini"]  # sorted


# --- empty / zero ----------------------------------------------------------


def test_empty_usage_is_zero():
    # Arrange / Act
    summary = summarize_usage({})
    # Assert
    assert summary == {
        "models": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }


def test_none_usage_is_zero():
    # Arrange / Act — defensive: None must not crash.
    summary = summarize_usage(None)
    # Assert
    assert summary["cost_usd"] == 0.0
    assert summary["models"] == []


def test_missing_token_fields_default_to_zero():
    # Arrange — a usage entry with no token counts at all.
    usage = {"gpt-4o": {}}
    # Act
    summary = summarize_usage(usage)
    # Assert
    assert summary["input_tokens"] == 0
    assert summary["output_tokens"] == 0
    assert summary["cost_usd"] == 0.0


# --- unknown model degrades gracefully -------------------------------------


def test_unknown_model_counts_tokens_but_adds_zero_cost():
    # Arrange — a model not in the pricing table.
    usage = {"some-future-model": _usage(1000, 1000)}
    # Act
    summary = summarize_usage(usage)
    # Assert — tokens still counted; cost contribution is zero (no crash).
    assert summary["input_tokens"] == 1000
    assert summary["output_tokens"] == 1000
    assert summary["cost_usd"] == 0.0


# --- dated-snapshot prefix fallback ----------------------------------------


def test_dated_snapshot_id_falls_back_to_base_pricing():
    # Arrange — a pinned snapshot id should resolve to gpt-4o pricing.
    usage = {"gpt-4o-2024-08-06": _usage(1000, 500)}
    # Act
    summary = summarize_usage(usage)
    # Assert — same cost as plain gpt-4o (0.0075), not zero.
    assert summary["cost_usd"] == 0.0075


def test_price_for_exact_match():
    # Arrange / Act / Assert — exact hits resolve directly.
    assert _price_for("gpt-4o") == _PRICING_PER_1K["gpt-4o"]
    assert _price_for("gpt-4o-mini") == _PRICING_PER_1K["gpt-4o-mini"]


@pytest.mark.xfail(
    reason=(
        "BUG: _price_for iterates _PRICING_PER_1K in insertion order and uses "
        "startswith, so a dated gpt-4o-mini snapshot (e.g. gpt-4o-mini-2024-07-18) "
        "matches the 'gpt-4o' prefix FIRST and is priced as gpt-4o (~16x too "
        "expensive). Reported, not fixed (source change out of scope for this PR's "
        "test pass)."
    ),
    strict=True,
)
def test_dated_mini_snapshot_should_use_mini_pricing():
    # Arrange / Act / Assert — desired behaviour: mini snapshot → mini pricing.
    assert _price_for("gpt-4o-mini-2024-07-18") == _PRICING_PER_1K["gpt-4o-mini"]


def test_price_for_unknown_returns_none():
    # Arrange / Act / Assert
    assert _price_for("claude-sonnet-4-6") is None
