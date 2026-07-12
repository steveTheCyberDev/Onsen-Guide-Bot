"""Trip-planner LangGraph ``StateGraph`` — PR3c slots + elicit-loop + naive itinerary.

The **first use of LangGraph in the live /chat path** (the legacy ReAct agent in
``agent/agent.py`` is the only other user, behind ``CHAT_ENGINE=react``). This is a
hand-built ``StateGraph`` compiled with a checkpointer — deliberately NOT
``create_react_agent`` — so PR7 re-planning is purely additive (an edge + a node,
not a reshape). See docs/v3-trip-planner-plan.md §0 "LangGraph adoption decision"
for the four re-planning-readiness properties this module honours:

  1. Hand-built ``StateGraph`` (this file), not the prebuilt ReAct graph.
  2. Accumulating working state from day one (``TripState`` in state.py) — PR3b now
     fills ``slots`` (a ``TripSlots``) turn over turn.
  3. A discrete ``plan`` node — PR3c makes it real (retrieve onsen per region +
     assemble a naive itinerary deterministically) WITHOUT reshaping the graph; PR7
     hangs a ``check_constraints`` node + conditional back-edge off it.
  4. Checkpointer wired (``MemorySaver`` local; ``PostgresSaver`` deferred behind
     ``trip_checkpointer_backend`` until PR1's Railway Postgres lands).

Shape: ``START → gather → {elicit | plan} → END``. Each turn the ``gather`` node
extracts slots from the message and merges them into the running ``TripSlots``; a
conditional edge then routes to ``elicit`` (ask ONE follow-up, no tool/LLM calls)
when a required slot is missing OR when a named region is unknown/non-Japan
(validated against the ingested-prefecture set — "reject early", 2026-07-12), or to
the discrete ``plan`` node once all required slots are present AND valid. The mixed
"Gifu and Texas" case therefore elicits (naming Texas) rather than quietly building
a Gifu-only itinerary. The ``plan`` node (PR3c)
retrieves onsen candidates per region via ``query_onsen_structured`` and assembles a
naive itinerary in pure Python (no LLM in the plan path), populating ``candidates`` +
``itinerary`` in state. Slots are checkpointed per ``thread_id = session_id``, so a
follow-up on a later turn resumes with the prior slots intact — the multi-turn
elicit-loop, now feeding a real itinerary.
"""

import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agent.trip.itinerary import assemble_trip
from agent.trip.slots import (
    TripSlots,
    elicit_message,
    extract_slots,
    invalid_regions,
    missing_required,
    should_elicit,
)
from agent.trip.state import TripState
from core.config import settings
from services.retrieval.retrieval_service import known_prefectures

logger = logging.getLogger(__name__)


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
    """Conditional edge: elicit if a required slot is missing OR invalid, else plan.

    Runs on the state AFTER ``gather`` merged this turn's slots, so it sees the
    freshest ``TripSlots``. Routes to ``elicit`` when a required slot is missing OR
    when the named regions include an unknown/non-Japan one — so a mixed
    "Gifu and Texas" request never reaches ``plan`` (region validity is part of the
    ``regions`` slot being satisfied). Returns the name of the next node.
    """
    slots = TripSlots(**(state.get("slots") or {}))
    return "elicit" if should_elicit(slots, known_prefectures()) else "plan"


def _elicit_node(state: TripState) -> dict:
    """Ask exactly ONE focused follow-up: a missing slot's question OR a bad region.

    Cheap by design: no tool/LLM calls (only the cached ``known_prefectures`` set).
    Precedence (via ``elicit_message``): regions present but invalid → the tailored
    "I only plan Japanese onsen trips" message naming the bad region(s); otherwise
    the first missing required slot's question. Ends the turn; the next turn
    re-enters ``gather`` and merges the user's correction, so a valid replacement
    (e.g. "just Gifu") lets the flow proceed to ``plan``.
    """
    slots = TripSlots(**(state.get("slots") or {}))
    known = known_prefectures()
    message = elicit_message(slots, known)
    logger.info(
        "trip.elicit node | missing=%s | invalid_regions=%s",
        missing_required(slots)[:1],
        invalid_regions(slots, known),
    )
    # elicit_message is only reached on the elicit branch, so it is never None here;
    # fall back defensively to a generic prompt rather than surfacing an empty reply.
    return {"reply": message or "Could you tell me a bit more about your trip?"}


def _plan_node(state: TripState) -> dict:
    """The discrete ``plan`` node — PR3c naive itinerary builder.

    Reached only when every required slot is present. Retrieves onsen candidates
    per region (the first ``services/`` call from ``agent/trip/``) and assembles a
    naive itinerary DETERMINISTICALLY in Python — no LLM call here — mirroring the
    ``search`` mode's no-fabrication discipline. Writes ``candidates`` + ``itinerary``
    back into working state and surfaces a template ``reply``. Regions with no
    ingested data are recorded explicitly ("no onsen found for X"), never invented.

    The node's identity/name is load-bearing (re-planning-readiness property #3):
    PR7 hangs a ``check_constraints`` node + conditional back-edge INTO this same
    node without reshaping. No re-planning, hotels, routing, or Places here (PR5/6/7).

    Sync by design: ``query_onsen_structured`` is a blocking Chroma call, so
    LangGraph runs this node in its executor under ``ainvoke`` — the event loop is
    not blocked (same trade-off the workflow makes for retrieval).
    """
    slots = state.get("slots") or {}
    logger.info("trip.plan node | slots complete | regions=%s", slots.get("regions"))
    result = assemble_trip(slots)
    return {
        "candidates": result["candidates"],
        "itinerary": result["itinerary"],
        "reply": result["reply"],
    }


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
    discrete ``plan`` node (all required present). Compiled WITH a checkpointer
    so ``TripSlots`` is retained per ``thread_id`` across ``ainvoke`` calls — the
    multi-turn elicit-loop. PR7 extends the ``plan`` node without reshaping this.
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
