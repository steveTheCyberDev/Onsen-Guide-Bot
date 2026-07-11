"""Trip-planner agent entry point — PR3a LangGraph skeleton.

The seam wired behind the ``trip_enabled`` gate (default False) by
``agent/workflow/pipeline.py``. PR2 shipped this as a dead canned-reply stub; PR3a
turns it into a **hand-built LangGraph ``StateGraph`` invocation** while preserving
the exact ``plan_trip(message, session_id, callbacks=...)`` signature so the
pipeline needs no reshaping.

Behaviour is still inert — the graph's single ``plan`` node makes NO LLM/tool/
service calls and returns the same canned reply (see ``graph.py``). What changed is
that the reply now flows THROUGH a checkpointed graph: working state (``TripState``)
persists per ``thread_id = session_id`` via ``MemorySaver``, so a second turn on the
same session observes accumulated state. Slots, elicitation, and itinerary logic
land in PR3b/3c — see docs/v3-trip-planner-plan.md §6.

Layering: this module imports shared schemas from ``agent/`` but never from
``api/``, and makes no ``services/`` calls in this slice.
"""

import logging

from agent.agent import AgentResponse
from agent.trip.graph import TRIP_STUB_REPLY, trip_graph

logger = logging.getLogger(__name__)

# Backward-compatible alias: the PR2 tests import ``_TRIP_STUB_REPLY`` from this
# module. The single source of truth now lives in graph.py (the node emits it).
_TRIP_STUB_REPLY = TRIP_STUB_REPLY


async def plan_trip(
    message: str, session_id: str, callbacks: list | None = None
) -> AgentResponse:
    """Run the trip-planner graph for one turn and return its ``AgentResponse``.

    Drives the compiled ``StateGraph`` with the checkpointer keyed by
    ``thread_id = session_id``, so state accumulates across turns of the same
    session. In PR3a the graph is inert (one stub ``plan`` node, canned reply, no
    LLM/tool/service calls), so the ``AgentResponse`` contract is unchanged —
    additive only: the stub reply, empty ``onsens``/``hotels``, ``recommendation=None``.

    Args:
        message: The latest user message. Passed into graph state as ``message``.
        session_id: Conversation/session id — used as the checkpointer ``thread_id``.
        callbacks: Optional LangChain callbacks for token-usage capture. Accepted to
            match the shape later PRs need (and to keep the pipeline call unchanged);
            threaded into the graph config so PR3b/3c LLM calls are captured, but no
            LLM runs in 3a.

    Returns:
        An ``AgentResponse``: the stub reply, empty ``onsens``/``hotels``,
        ``recommendation=None``. The ``/chat`` response contract is unchanged.
    """
    logger.info("plan_trip | session_id=%s", session_id)
    config: dict = {"configurable": {"thread_id": session_id}}
    if callbacks:
        config["callbacks"] = callbacks
    result = await trip_graph.ainvoke({"message": message}, config=config)
    reply = result.get("reply", TRIP_STUB_REPLY)
    return AgentResponse(
        reply=reply,
        onsens=[],
        hotels=[],
        recommendation=None,
    )
