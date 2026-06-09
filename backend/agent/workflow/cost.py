"""Per-request cost/token accounting for the V2 workflow.

Lives in ``agent/workflow/`` so the ``services/`` layer stays LangChain-agnostic:
all token-usage capture happens via a LangChain callback handler attached at the
workflow layer, never inside a service.

Usage:

    from langchain_core.callbacks import UsageMetadataCallbackHandler
    from agent.workflow.cost import summarize_usage

    cb = UsageMetadataCallbackHandler()
    # ... run LLM calls with config={"callbacks": [cb]} ...
    summary = summarize_usage(cb.usage_metadata)

``UsageMetadataCallbackHandler.usage_metadata`` is a dict keyed by model name,
each value a usage-metadata dict with ``input_tokens`` / ``output_tokens`` /
``total_tokens``. The intent call uses ``.with_structured_output``, so usage is
NOT on the returned object — the callback is the reliable capture point.
"""

# Pricing in USD per 1,000 tokens. PRICES DRIFT — these are the published
# OpenAI rates as of 2026-06-09; revisit when models or pricing change. Models
# not listed here contribute 0 to the cost estimate (logged tokens still count),
# so an unknown model degrades gracefully rather than crashing.
_PRICING_PER_1K: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 0.0025, "output": 0.010},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
}


def _price_for(model: str) -> dict[str, float] | None:
    """Resolve a pricing entry for a model name.

    Tolerates dated/suffixed ids (e.g. ``gpt-4o-2024-08-06``) by falling back to
    a known prefix match so cost estimation does not silently drop to zero when a
    pinned snapshot id is used.
    """
    if model in _PRICING_PER_1K:
        return _PRICING_PER_1K[model]
    for known, price in _PRICING_PER_1K.items():
        if model.startswith(known):
            return price
    return None


def summarize_usage(usage_metadata: dict) -> dict:
    """Summarize per-model token usage into totals + an estimated USD cost.

    Args:
        usage_metadata: The ``usage_metadata`` dict from a
            ``UsageMetadataCallbackHandler`` — keyed by model name, each value a
            usage dict carrying ``input_tokens`` / ``output_tokens``.

    Returns:
        A dict with ``models`` (sorted list of model names used),
        ``input_tokens``, ``output_tokens`` (summed across models), and
        ``cost_usd`` (estimated, rounded to 6 dp).
    """
    input_tokens = 0
    output_tokens = 0
    cost = 0.0
    models: list[str] = []

    for model, usage in (usage_metadata or {}).items():
        models.append(model)
        in_tok = int(usage.get("input_tokens", 0) or 0)
        out_tok = int(usage.get("output_tokens", 0) or 0)
        input_tokens += in_tok
        output_tokens += out_tok
        price = _price_for(model)
        if price is not None:
            cost += in_tok / 1000 * price["input"] + out_tok / 1000 * price["output"]

    return {
        "models": sorted(models),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 6),
    }
