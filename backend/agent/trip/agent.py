"""Trip-planner agent entry point — PR2 DEAD STUB.

This is the placeholder wired behind the ``trip_enabled`` gate (default False) by
``agent/workflow/pipeline.py``. It makes NO service calls, NO LLM calls, and runs
NO tool logic — it only returns a clearly-marked canned ``AgentResponse`` so the
routing seam (Intent.mode == "trip" → this module) can be exercised end-to-end
while shipping dead in prod.

The real trip-planner (slot-filling elicit-loop, LangGraph agent, onsen/hotel/
routing tools, re-planning) lands in later PRs — see docs/v3-trip-planner-plan.md
§6 (PR3+). Layering: this module may import shared schemas from ``agent/`` but
never from ``api/``, and calls no ``services/`` code in this stub.
"""

import logging

from agent.agent import AgentResponse

logger = logging.getLogger(__name__)

# Canned reply returned while the trip-planner is not yet built. Mirrors the tone
# of the ask-mode stub in pipeline.py; points the user at the modes that DO work.
_TRIP_STUB_REPLY = (
    "Trip planning is coming soon — for now I can search onsen, recommend picks, "
    "or answer onsen questions."
)


async def plan_trip(
    message: str, session_id: str, callbacks: list | None = None
) -> AgentResponse:
    """Return the PR2 trip-planner stub response.

    Args:
        message: The latest user message (unused in the stub).
        session_id: Conversation/session identifier (unused in the stub).
        callbacks: Optional LangChain callbacks for token-usage capture. Accepted
            to match the shape later PRs will need; unused here (no LLM call).

    Returns:
        A canned ``AgentResponse``: the stub reply, empty ``onsens``/``hotels``,
        and ``recommendation=None``. The ``/chat`` response contract is unchanged.
    """
    logger.info("plan_trip STUB | session_id=%s", session_id)
    return AgentResponse(
        reply=_TRIP_STUB_REPLY,
        onsens=[],
        hotels=[],
        recommendation=None,
    )
