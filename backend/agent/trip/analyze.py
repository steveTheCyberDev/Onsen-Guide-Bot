"""Grounded RECOMMEND brain for the trip-planner (V3 pc1 — trip analyze).

A single grounded LLM call that turns the SETTLED trip itinerary into a *judged*
result: grounded per-stop pros/cons plus one top-level recommendation explaining
why this itinerary suits the traveller. It mirrors the recommend-mode brain
(``agent/workflow/analyze.py::analyze_onsen``) — same STRICT GROUNDING discipline —
but is trip-framed: the "preference" is the structured ``TripSlots`` (regions,
nights, pace, spring/scenery prefs), and the recommendation reasons over the WHOLE
itinerary rather than picking a single onsen.

GROUNDING is the whole point (this project's identity): the prompt feeds a COMPACT
projection of each stop (name, spring_type, location, truncated description) plus
the traveller's slots, and instructs the model to derive pros/cons ONLY from those
literal fields — never inventing facilities, prices, tattoo policies, scenery, or a
NEW onsen/stop. The LLM may WEIGH the provided stops against the slots; it may not
assert a new fact or invent a stop (the itinerary itself is still assembled
deterministically upstream in ``itinerary.py``). Coordinates and URLs are
deliberately NOT sent — no judgement value, just tokens.

Layering: imports the NEUTRAL ``agent/grounding.py`` (shared anti-fabrication
contract + projection, single-sourced with the recommend brain) + ``core.config``
— never ``api/`` and never ``agent/workflow`` internals. Using the neutral shared
module keeps the established decoupling stance (cf. ``itinerary.py`` keeping
``_ONSEN_FIELDS`` / ``_to_hotel`` local) while removing the grounding-text drift
risk between the two brains.

Cost: usage is captured the same way as the rest of the trip graph — via the
ambient LangChain callbacks propagated from ``plan_trip``'s config (the graph
threads them into every nested LLM call), so ``agent/workflow/cost.py`` sees this
call's tokens without the node passing callbacks explicitly. The optional
``callbacks`` arg mirrors ``analyze_onsen`` for direct/unit callers.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agent.grounding import (
    STRICT_GROUNDING_RULES,
    OnsenAnalysis,
    project_candidates,
)
from agent.schemas import OnsenResult
from core.config import settings

logger = logging.getLogger(__name__)

# The per-candidate schema + projection helper are the shared, single-sourced ones
# from agent/grounding.py — same objects the recommend brain uses, so the pros/cons
# contract and projection format can't drift. Aliased under the historical private
# names for this module's tests/importers.
_OnsenAnalysis = OnsenAnalysis
_project = project_candidates


class TripGuideResult(BaseModel):
    """Structured output of the trip analyze brain."""

    analyses: list[OnsenAnalysis] = Field(
        default=[],
        description="One entry per itinerary onsen stop, keyed by its 0-based index.",
    )
    recommendation: str = Field(
        description=(
            "One short paragraph explaining why THIS itinerary (its regions, stops, "
            "pace and nights) suits the traveller's stated preferences — grounded "
            "only in the provided stop data + slots. Judge/weigh the provided stops; "
            "never assert a new fact about a stop or introduce a stop not listed."
        )
    )


# Task-framing intro (trip mode: judge the WHOLE itinerary). The anti-fabrication
# contract itself is the shared STRICT_GROUNDING_RULES (single source of truth with
# the recommend brain). The trip-specific tail keeps trip's stronger "never introduce
# a stop not listed" rule + the itinerary-scoped recommendation bullet.
_INTRO = (
    "You are an expert guide for Japanese hot-spring (onsen) trips. You are given a "
    "traveller's trip parameters (regions, nights, pace, and any spring/scenery "
    "preferences) and the SETTLED itinerary: a numbered list of the real onsen "
    "stops it visits (each with name, spring type, location, and a short "
    "description). For each stop give a few short pros and cons, then write ONE "
    "short recommendation explaining why this itinerary as a whole suits the "
    "traveller."
)
_TRIP_TAIL = (
    "- NEVER introduce an onsen or stop that is not in the provided numbered list.\n"
    "- The recommendation may compare the itinerary's spring types, locations, pace "
    "and nights against the traveller's preferences, but must not assert any fact "
    "absent from the data."
)
_INSTRUCTIONS = f"{_INTRO}\n{STRICT_GROUNDING_RULES}\n{_TRIP_TAIL}"

# Construct the analyze model once at import time. Reuses the analyze_model knob
# (default gpt-4o) — the same heavier judgement path as the recommend brain, kept
# distinct from the cheap intent/slot models. Structured output binds
# TripGuideResult so the call returns a validated instance directly. Tests patch
# this module-level object so no real OpenAI call is made.
_llm = ChatOpenAI(
    model=settings.analyze_model,
    api_key=settings.openai_api_key,
    stream_usage=True,
).with_structured_output(TripGuideResult)


def _trip_context(slots: dict) -> str:
    """Render the traveller's structured trip parameters as the 'preference' block.

    This is the trip analogue of the recommend brain's free-text preference: the
    slots ARE the statement of what the traveller wants. Only the judgement-relevant
    slots are sent (regions, nights, pace, spring/scenery prefs) — the rest carry no
    judgement value.
    """
    regions = ", ".join(slots.get("regions") or []) or "unspecified"
    nights = slots.get("nights")
    nights_str = str(nights) if nights is not None else "unspecified"
    pace = slots.get("pace") or "relaxed"
    prefs = (slots.get("spring_or_scenery_prefs") or "").strip() or "none stated"
    return (
        f"Regions: {regions}\n"
        f"Nights: {nights_str}\n"
        f"Pace: {pace}\n"
        f"Spring/scenery preferences: {prefs}"
    )


async def analyze_itinerary(
    slots: dict, onsens: list[OnsenResult], callbacks: list | None = None
) -> tuple[list[OnsenResult], str | None]:
    """Attach grounded pros/cons to the itinerary stops + a trip recommendation.

    Args:
        slots: The serialised ``TripSlots`` dict (regions, nights, pace,
            spring_or_scenery_prefs) — the traveller's stated preferences.
        onsens: The itinerary's real selected onsen stops (already projected onto
            ``OnsenResult``). Mutated in place to attach pros/cons (and returned for
            convenience).
        callbacks: Optional LangChain callbacks for token-usage capture; threaded
            into the run config. Within the trip graph, usage is ALSO captured via
            the ambient config propagated from ``plan_trip``, so passing None from
            the graph node is fine.

    Returns:
        ``(onsens, recommendation)`` — the same stop list with pros/cons filled in
        by index, and the top-level recommendation string (None only when there were
        no stops to analyze).
    """
    if not onsens:
        logger.info("analyze_itinerary | no stops — skipping LLM call")
        return onsens, None

    messages = [
        SystemMessage(content=_INSTRUCTIONS),
        HumanMessage(
            content=(
                f"Traveller's trip:\n{_trip_context(slots)}\n\n"
                f"Itinerary onsen stops:\n{_project(onsens)}"
            )
        ),
    ]
    run_config: dict = {
        "run_name": "analyze-itinerary",
        "tags": ["trip", "analyze", f"model:{settings.analyze_model}"],
        "metadata": {
            "node": "analyze_itinerary",
            "analyze_model": settings.analyze_model,
            "version": "v3-trip",
        },
    }
    if callbacks:
        run_config["callbacks"] = callbacks

    result: TripGuideResult = await _llm.ainvoke(messages, config=run_config)

    # Merge pros/cons back by index; ignore out-of-range indices defensively — the
    # LLM cannot introduce a stop, only annotate the ones we assembled.
    for a in result.analyses:
        if 0 <= a.index < len(onsens):
            onsens[a.index].pros = a.pros
            onsens[a.index].cons = a.cons
        else:
            logger.warning(
                "analyze_itinerary | dropping out-of-range index=%s", a.index
            )

    logger.info("analyze_itinerary | analyzed=%d stops", len(onsens))
    return onsens, result.recommendation
