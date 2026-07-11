"""Trip-planner LangGraph ``StateGraph`` — PR3b slots + elicit-loop.

The **first use of LangGraph in the live /chat path** (the legacy ReAct agent in
``agent/agent.py`` is the only other user, behind ``CHAT_ENGINE=react``). This is a
hand-built ``StateGraph`` compiled with a checkpointer — deliberately NOT
``create_react_agent`` — so PR7 re-planning is purely additive (an edge + a node,
not a reshape). See docs/v3-trip-planner-plan.md §0 "LangGraph adoption decision"
for the four re-planning-readiness properties this module honours:

  1. Hand-built ``StateGraph`` (this file), not the prebuilt ReAct graph.
  2. Accumulating working state from day one (``TripState`` in state.py) — PR3b now
     fills ``slots`` (a ``TripSlots``) turn over turn.
  3. A discrete ``plan`` node — 3b keeps it a placeholder ("got everything, planning
     next"); PR3c makes it real without reshaping; PR7 hangs a ``check_constraints``
     node + conditional back-edge off it.
  4. Checkpointer wired (``MemorySaver`` local; ``PostgresSaver`` deferred behind
     ``trip_checkpointer_backend`` until PR1's Railway Postgres lands).

Shape (3b): ``START → gather → {elicit | plan} → END``. Each turn the ``gather``
node extracts slots from the message and merges them into the running ``TripSlots``;
a conditional edge then routes to ``elicit`` (ask ONE follow-up for the first missing
required slot, no tool/service calls) when a required slot is missing, or to the
discrete ``plan`` placeholder once all required slots are present. Slots are
checkpointed per ``thread_id = session_id``, so a follow-up on a later turn resumes
with the prior slots intact — the multi-turn elicit-loop that IS the point of PR3b.
"""

import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agent.trip.slots import (
    TripSlots,
    extract_slots,
    missing_required,
    next_question,
)
from agent.trip.state import TripState
from core.config import settings

logger = logging.getLogger(__name__)

# Placeholder reply for the discrete ``plan`` node once every required slot is
# filled. PR3c replaces this with a real assembled itinerary; for 3b it is a cheap
# acknowledgement that elicitation is complete and planning is next. No LLM/tool
# calls here in 3b.
PLAN_ACK_REPLY = (
    "Great — I have the essentials for your onsen trip. Putting an itinerary "
    "together next."
)


async def _gather_node(state: TripState) -> dict:
    """Extract slots from the latest message and merge into the running state.

    Reconstructs the accumulated ``TripSlots`` from the loaded checkpoint (stored as
    a plain dict so the state stays JSON-serialisable for the future PostgresSaver),
    runs the structured-output extraction, and writes the merged slots back. Also
    bumps ``turn_count`` here — ``gather`` runs on EVERY turn regardless of which
    branch follows, so it is the single place accumulating-state proof lives.
    """
    current = TripSlots(**(state.get("slots") or {}))
    merged = await extract_slots(state.get("message", ""), current)
    turn = state.get("turn_count", 0) + 1
    logger.info(
        "trip.gather node | turn_count=%d | missing_required=%s",
        turn,
        missing_required(merged),
    )
    return {"slots": merged.model_dump(), "turn_count": turn}


def _route_after_gather(state: TripState) -> str:
    """Conditional edge: elicit if a required slot is missing, else plan.

    Runs on the state AFTER ``gather`` merged this turn's slots, so it sees the
    freshest ``TripSlots``. Returns the name of the next node.
    """
    slots = TripSlots(**(state.get("slots") or {}))
    return "elicit" if missing_required(slots) else "plan"


def _elicit_node(state: TripState) -> dict:
    """Ask exactly ONE focused follow-up for the first missing required slot.

    Cheap by design: no tool/service/LLM calls — just a canned question keyed off
    the highest-priority missing slot (``regions → nights → dates_or_season``). Ends
    the turn; the next turn re-enters ``gather`` with the prior slots loaded from the
    checkpoint and merges the user's answer.
    """
    slots = TripSlots(**(state.get("slots") or {}))
    question = next_question(slots)
    logger.info("trip.elicit node | asking for=%s", missing_required(slots)[:1])
    # next_question is only reached on the elicit branch, so it is never None here;
    # fall back defensively to a generic prompt rather than surfacing an empty reply.
    return {"reply": question or "Could you tell me a bit more about your trip?"}


def _plan_node(state: TripState) -> dict:
    """The discrete ``plan`` node — PR3b placeholder.

    Reached only when every required slot is present. 3b acknowledges readiness and
    stops; PR3c replaces this body with real itinerary assembly over ``candidates``,
    and PR7 hangs a ``check_constraints`` node + conditional back-edge INTO this same
    node. The node's identity/name is load-bearing — keep it ``plan``.
    """
    logger.info("trip.plan node (placeholder) | slots complete")
    return {"reply": PLAN_ACK_REPLY}


def _build_checkpointer():
    """Construct the checkpointer selected by ``trip_checkpointer_backend``.

    "memory" → in-process ``MemorySaver`` (local default). "postgres" is deferred
    behind the flag until PR1's Railway Postgres exists; selecting it now raises a
    clear ``NotImplementedError`` rather than silently mis-persisting. This is the
    single STOP point this slice must not cross (no Railway/Postgres wiring).
    """
    backend = settings.trip_checkpointer_backend
    if backend == "memory":
        return MemorySaver()
    if backend == "postgres":
        # Deferred: depends on Step-0 Postgres (PR1). See §0/§4 of the plan.
        raise NotImplementedError(
            "trip_checkpointer_backend='postgres' (PostgresSaver) is deferred until "
            "PR1 Railway Postgres lands; use 'memory' for now."
        )
    raise ValueError(
        f"Unknown trip_checkpointer_backend={backend!r} (expected 'memory' or 'postgres')"
    )


def build_trip_graph():
    """Build and compile the trip-planner ``StateGraph``.

    Shape: ``START → gather → {elicit | plan} → END``. ``gather`` extracts + merges
    slots; a conditional edge routes to ``elicit`` (missing required slot) or the
    discrete ``plan`` placeholder (all required present). Compiled WITH a checkpointer
    so ``TripSlots`` is retained per ``thread_id`` across ``ainvoke`` calls — the
    multi-turn elicit-loop. 3c/PR7 extend the ``plan`` node without reshaping this.
    """
    builder = StateGraph(TripState)
    builder.add_node("gather", _gather_node)
    builder.add_node("elicit", _elicit_node)
    builder.add_node("plan", _plan_node)
    builder.add_edge(START, "gather")
    builder.add_conditional_edges(
        "gather", _route_after_gather, {"elicit": "elicit", "plan": "plan"}
    )
    builder.add_edge("elicit", END)
    builder.add_edge("plan", END)
    return builder.compile(checkpointer=_build_checkpointer())


# Module-level singleton, mirroring the ReAct ``graph`` in agent/agent.py. Compiling
# once at import keeps the in-process MemorySaver alive for the whole process, so
# slots accumulate across /chat requests keyed by thread_id=session_id.
trip_graph = build_trip_graph()
