"""RECOMMEND brain for the V2.5 workflow — ``analyze_onsen``.

A single LLM call that turns a list of candidate onsen into a *judged* result:
grounded per-onsen pros/cons plus one top-level recommendation. This is the
first LLM call in the V2 redesign that earns its place back — it adds judgement,
not serialization (the deterministic Python data layer still assembles the
facts; this layer only reasons over them).

GROUNDING is the whole point: the prompt feeds a COMPACT projection of each
candidate (name, spring_type, location, a truncated description) and the user's
stated preference, and instructs the model to derive pros/cons ONLY from those
fields — never inventing facilities, prices, or tattoo policies absent from the
data. Coordinates and URLs are deliberately NOT sent (no judgement value, just
tokens).

The structured output is keyed by candidate index so pros/cons merge back onto
the matching ``OnsenResult`` deterministically.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agent.grounding import (
    DESC_MAX_CHARS,
    STRICT_GROUNDING_RULES,
    OnsenAnalysis,
    project_candidates,
)
from agent.schemas import OnsenResult
from core.config import settings

logger = logging.getLogger(__name__)

# The per-candidate schema, projection helper, and description cap now live in the
# neutral agent/grounding.py (single source of truth, shared with the trip analyze
# brain). Re-exported under their historical private names so existing importers/
# tests keep working; new code should import from agent.grounding directly.
_DESC_MAX_CHARS = DESC_MAX_CHARS
_OnsenAnalysis = OnsenAnalysis
_project = project_candidates


class GuideResult(BaseModel):
    """Structured output of the analyze_onsen brain."""

    analyses: list[OnsenAnalysis] = Field(
        default=[],
        description="One entry per candidate onsen, keyed by its 0-based index.",
    )
    recommendation: str = Field(
        description=(
            "One short paragraph recommending which onsen best fits the user's "
            "stated preference, and why — grounded only in the provided data."
        )
    )


# Task-framing intro (recommend mode: pick ONE onsen). The anti-fabrication contract
# itself is the shared STRICT_GROUNDING_RULES; the final domain-specific bullet (what
# the recommendation may compare) stays here. Composed so the effective prompt is
# byte-identical to the pre-refactor _INSTRUCTIONS.
_INTRO = (
    "You are an expert guide for Japanese hot springs (onsen). You are given a "
    "numbered list of candidate onsen (each with name, spring type, location, and "
    "a short description) and the traveller's stated preference. For each onsen, "
    "give a few short pros and cons, and then recommend which one best fits the "
    "preference and why."
)
_RECOMMEND_TAIL = (
    "- The recommendation paragraph may compare spring type and location against "
    "the preference, but must not assert any fact absent from the data."
)
_INSTRUCTIONS = f"{_INTRO}\n{STRICT_GROUNDING_RULES}\n{_RECOMMEND_TAIL}"

# Construct the analyze model once at import time. Uses the analyze_model knob
# (default gpt-4o) — the heavier judgement path, distinct from the cheap
# intent_model. Structured output binds GuideResult so the call returns a
# validated instance directly.
_llm = ChatOpenAI(
    model=settings.analyze_model,
    api_key=settings.openai_api_key,
    stream_usage=True,
).with_structured_output(GuideResult)


async def analyze_onsen(
    query: str, onsens: list[OnsenResult], callbacks: list | None = None
) -> tuple[list[OnsenResult], str | None]:
    """Attach grounded pros/cons to candidates and produce a recommendation.

    Args:
        query: The user's free-text preference (the Intent.query). There is no
            preference elicitation in this MVP — this string is the only
            statement of what the user wants.
        onsens: Candidate onsen from the deterministic retrieval step. Mutated
            in place to attach pros/cons (and returned for convenience).
        callbacks: Optional LangChain callbacks (e.g. a
            ``UsageMetadataCallbackHandler``) for token-usage capture; this call
            uses ``.with_structured_output`` so usage is not on the return value.

    Returns:
        ``(onsens, recommendation)`` — the same onsen list with pros/cons filled
        in by index, and the top-level recommendation string (None only if there
        were no candidates to analyze).
    """
    if not onsens:
        logger.info("analyze_onsen | no candidates — skipping LLM call")
        return onsens, None

    messages = [
        SystemMessage(content=_INSTRUCTIONS),
        HumanMessage(
            content=(
                f"Traveller's preference: {query}\n\n"
                f"Candidate onsen:\n{_project(onsens)}"
            )
        ),
    ]
    run_config: dict = {
        "run_name": "analyze-onsen",
        "tags": ["workflow", "analyze", f"model:{settings.analyze_model}"],
        "metadata": {
            "node": "analyze_onsen",
            "analyze_model": settings.analyze_model,
            "version": "v2-workflow",
        },
    }
    if callbacks:
        run_config["callbacks"] = callbacks

    result: GuideResult = await _llm.ainvoke(messages, config=run_config)

    # Merge pros/cons back by index; ignore out-of-range indices defensively.
    for a in result.analyses:
        if 0 <= a.index < len(onsens):
            onsens[a.index].pros = a.pros
            onsens[a.index].cons = a.cons
        else:
            logger.warning("analyze_onsen | dropping out-of-range index=%s", a.index)

    logger.info("analyze_onsen | analyzed=%d candidates", len(onsens))
    return onsens, result.recommendation
