"""Trip-planner LangGraph ``StateGraph`` — PR3a skeleton.

The **first use of LangGraph in the live /chat path** (the legacy ReAct agent in
``agent/agent.py`` is the only other user, behind ``CHAT_ENGINE=react``). This is a
hand-built ``StateGraph`` compiled with a checkpointer — deliberately NOT
``create_react_agent`` — so PR7 re-planning is purely additive (an edge + a node,
not a reshape). See docs/v3-trip-planner-plan.md §0 "LangGraph adoption decision"
for the four re-planning-readiness properties this module honours:

  1. Hand-built ``StateGraph`` (this file), not the prebuilt ReAct graph.
  2. Accumulating working state from day one (``TripState`` in state.py).
  3. A discrete ``plan`` node — 3a stubs it; PR3c makes it real without reshaping
     the graph; PR7 hangs a ``check_constraints`` node + conditional back-edge off it.
  4. Checkpointer wired (``MemorySaver`` local; ``PostgresSaver`` deferred behind
     ``trip_checkpointer_backend`` until PR1's Railway Postgres lands).

PR3a behaviour is intentionally inert: the ``plan`` node makes NO LLM/tool/service
calls and returns the same canned "coming soon" reply as the PR2 stub. What changed
is that the reply now flows THROUGH a checkpointed graph, so working state persists
per ``thread_id = session_id``.
"""

import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agent.trip.state import TripState
from core.config import settings

logger = logging.getLogger(__name__)

# Canned reply while the trip-planner is not yet built. Single source of truth —
# agent.py re-exports it as ``_TRIP_STUB_REPLY`` for the existing PR2 tests. Mirrors
# the ask-mode stub's tone; points the user at the modes that DO work today.
TRIP_STUB_REPLY = (
    "Trip planning is coming soon — for now I can search onsen, recommend picks, "
    "or answer onsen questions."
)


def _plan_node(state: TripState) -> dict:
    """The discrete ``plan`` node (PR3a stub).

    3a does no planning: it returns the canned reply and bumps ``turn_count`` so
    the checkpointer has observable accumulating state. It reads the prior value
    from the loaded checkpoint and returns the increment explicitly (rather than
    relying on a reducer channel's initial value), which is version-robust and
    makes the two-turn persistence assertion unambiguous.

    PR3c replaces this body with real itinerary assembly over ``candidates``;
    PR7 adds a ``check_constraints`` node + a conditional back-edge INTO this same
    node. The node's identity/name is therefore load-bearing — keep it ``plan``.
    """
    turn = state.get("turn_count", 0) + 1
    logger.info("trip.plan node (stub) | turn_count=%d", turn)
    return {"reply": TRIP_STUB_REPLY, "turn_count": turn}


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

    Shape: ``START → plan → END``. A single discrete node is sufficient for 3a;
    the structure is chosen so 3b (elicit node + slots-complete conditional) and
    PR7 (constraints back-edge) slot in without reshaping. Compiled WITH a
    checkpointer so state is retained per ``thread_id`` across ``ainvoke`` calls.
    """
    builder = StateGraph(TripState)
    builder.add_node("plan", _plan_node)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", END)
    return builder.compile(checkpointer=_build_checkpointer())


# Module-level singleton, mirroring the ReAct ``graph`` in agent/agent.py. Compiling
# once at import keeps the in-process MemorySaver alive for the whole process, so
# state accumulates across /chat requests keyed by thread_id=session_id.
trip_graph = build_trip_graph()
