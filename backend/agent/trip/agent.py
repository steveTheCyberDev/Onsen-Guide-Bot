"""Trip-planner agent entry point — PR3b slots + elicit-loop.

The seam wired behind the ``trip_enabled`` gate (default False) by
``agent/workflow/pipeline.py``. PR2 shipped a dead canned-reply stub; PR3a made the
reply flow through a checkpointed ``StateGraph``; PR3b makes it **active**: each turn
the graph extracts slots from the message, merges them into the running
``TripSlots``, and either asks ONE follow-up (elicit) for the first missing required
slot or acknowledges readiness (the discrete ``plan`` placeholder). The
``plan_trip(message, session_id, callbacks=...)`` signature is preserved so the
pipeline needs no reshaping.

The ``AgentResponse`` contract stays additive-only: the reply is now the elicit
question or the planning acknowledgement, but ``onsens``/``hotels`` remain empty and
``recommendation`` stays ``None`` until itinerary assembly lands in PR3c. Slots are
checkpointed per ``thread_id = session_id`` via ``MemorySaver``, so a follow-up
answer on a later turn resumes with the prior slots intact.

Layering: this module imports shared schemas + LLM plumbing from ``agent/`` but never
from ``api/``, and makes no ``services/`` calls in this slice.
"""

import logging

from agent.agent import AgentResponse
from agent.trip.graph import trip_graph

logger = logging.getLogger(__name__)

# Defensive fallback if the graph ever returns without a reply (it always sets one
# on both the elicit and plan branches). Kept local to the entry point.
_FALLBACK_REPLY = "Could you tell me a bit more about your onsen trip?"


async def plan_trip(
    message: str, session_id: str, callbacks: list | None = None
) -> AgentResponse:
    """Run the trip-planner graph for one turn and return its ``AgentResponse``.

    Drives the compiled ``StateGraph`` with the checkpointer keyed by
    ``thread_id = session_id``, so ``TripSlots`` accumulate across turns of the same
    session. The reply is the graph's output — an elicit follow-up when a required
    slot is missing, or the planning acknowledgement once all required slots are
    present. The ``AgentResponse`` contract is additive-only: empty
    ``onsens``/``hotels``, ``recommendation=None`` (itinerary lands in PR3c).

    Args:
        message: The latest user message. Passed into graph state as ``message``.
        session_id: Conversation/session id — used as the checkpointer ``thread_id``.
        callbacks: Optional LangChain callbacks for token-usage capture, threaded
            into the graph config so the extraction LLM call's usage is captured.

    Returns:
        An ``AgentResponse``: the graph's reply, empty ``onsens``/``hotels``,
        ``recommendation=None``. The ``/chat`` response contract is unchanged.
    """
    logger.info("plan_trip | session_id=%s", session_id)
    config: dict = {"configurable": {"thread_id": session_id}}
    if callbacks:
        config["callbacks"] = callbacks
    result = await trip_graph.ainvoke({"message": message}, config=config)
    reply = result.get("reply") or _FALLBACK_REPLY
    return AgentResponse(
        reply=reply,
        onsens=[],
        hotels=[],
        recommendation=None,
    )
