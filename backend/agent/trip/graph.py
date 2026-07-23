"""Trip-planner LangGraph ``StateGraph`` — PR3c slots + elicit-loop + naive itinerary.

The **only use of LangGraph in the live /chat path** (the deterministic search/
recommend/ask workflow is LangGraph-free). This is a hand-built ``StateGraph``
compiled with a checkpointer — deliberately NOT a prebuilt agent graph — so PR7
re-planning is purely additive (an edge + a node, not a reshape). See
docs/v3-trip-planner-plan.md §0 "LangGraph adoption decision" for the four
re-planning-readiness properties this module honours:

  1. Hand-built ``StateGraph`` (this file), not a prebuilt agent graph.
  2. Accumulating working state from day one (``TripState`` in state.py) — PR3b now
     fills ``slots`` (a ``TripSlots``) turn over turn.
  3. A discrete ``plan`` node — PR3c makes it real (retrieve onsen per region +
     assemble a naive itinerary deterministically) WITHOUT reshaping the graph; PR7
     hangs a ``check_constraints`` node + conditional back-edge off it.
  4. Checkpointer wired (``MemorySaver`` local; ``PostgresSaver`` deferred behind
     ``trip_checkpointer_backend`` until PR1's Railway Postgres lands).

Shape (PR7): ``START → gather → {elicit → END | plan → check_constraints →
{plan (re-plan back-edge) | END}}``. The ``check_constraints`` node + conditional
back-edge hang off ``plan`` per property #3 WITHOUT reshaping the rest of the graph.
Each turn the ``gather`` node
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

from agent.trip.constraints import augment_reply, detect_conflicts
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
    """The discrete ``plan`` node — naive itinerary builder (PR3c) + re-plan target (PR7).

    Reached when every required slot is present, AND re-entered via the
    ``check_constraints`` back-edge (PR7) when a corrective re-plan is needed.
    Retrieves onsen candidates per region, assembles a naive itinerary
    DETERMINISTICALLY in Python — no LLM call here — looks up nearby hotels per
    selected onsen stop (PR5, fail-soft), and computes haversine region centroids +
    a nearest-neighbour stop route (PR7). Writes ``candidates`` + ``itinerary`` back
    into working state and surfaces the base template ``reply`` (``check_constraints``
    may then prefix it with conflict prose). Regions with no ingested data are
    recorded explicitly ("no onsen found for X"); stops with no nearby lodging say
    "no hotels found nearby" — never invented.

    PR7 re-plan: any region in ``dropped_regions`` (set by ``check_constraints`` on a
    prior pass) is EXCLUDED from this pass's effective regions, so the second pass
    builds a tighter itinerary over the kept regions. On the first pass
    ``dropped_regions`` is empty, so retrieval still covers every requested region
    (the tool-selection signal the eval reads).

    The node's identity/name is load-bearing (re-planning-readiness property #3):
    the ``check_constraints`` node + conditional back-edge hang off it WITHOUT
    reshaping the graph.

    Sync by design: ``query_onsen_structured`` (Chroma) and ``search_hotels``
    (Rakuten) are blocking calls, so LangGraph runs this node in its executor under
    ``ainvoke`` — the event loop is not blocked (same trade-off the workflow makes).
    """
    slots = dict(state.get("slots") or {})
    dropped = {d["region"] for d in (state.get("dropped_regions") or [])}
    requested = slots.get("regions", [])
    effective = [r for r in requested if r not in dropped]
    eff_slots = {**slots, "regions": effective}
    logger.info(
        "trip.plan node | requested=%s | dropped=%s | effective=%s",
        requested, sorted(dropped), effective,
    )
    result = assemble_trip(eff_slots)
    return {
        "candidates": result["candidates"],
        "itinerary": result["itinerary"],
        "reply": result["reply"],
    }


def _check_constraints_node(state: TripState) -> dict:
    """PR7 re-planning node — detect deterministic conflicts + explain the plan.

    Runs AFTER ``plan`` on the just-assembled itinerary. Applies two honest,
    haversine-based rules (see ``agent/trip/constraints.py``) over the itinerary's
    region centroids — NO LLM call, NO Google billing:

      * over-constrained (≥3 dispersed regions the nights can't absorb) → record the
        farthest-outlier region in ``dropped_regions``, bump ``replan_count``, and
        signal the ``plan`` back-edge (``pending_replan=True``) so the plan is rebuilt
        without it;
      * geographic infeasibility (two regions too far apart for one land trip) →
        set the advisory ``infeasible`` flag (never a re-plan — can't be fixed by
        dropping) so the reply flags it + suggests a reshape.

    On the FINAL pass (no further re-plan) it prefixes the base itinerary reply with
    truthful conflict prose naming the conflict, the tradeoff, and any dropped region
    + reason. When no conflict is detected it leaves the reply unchanged, so a
    single-region / compact trip is byte-identical to the pre-PR7 output.
    """
    slots = state.get("slots") or {}
    itinerary = state.get("itinerary") or {}
    dropped = list(state.get("dropped_regions") or [])
    replan_count = state.get("replan_count", 0)
    requested = slots.get("regions", [])
    centroids = itinerary.get("region_centroids") or {}

    report = detect_conflicts(slots, centroids, replan_count)

    # Infeasibility can be detected on an EARLIER pass than the one that finalises
    # the reply (a trip that is BOTH infeasible and over-constrained flags it on
    # pass 1, then drops the outlier and finalises on pass 2 — by which point the
    # far region may be gone and the fresh report no longer sees it). Persist the
    # flag across passes so the final reply still notes it: OR this pass's finding
    # with anything already recorded on state.
    infeasible = report.infeasible or state.get("infeasible")

    if report.should_replan and report.drop_region:
        new_dropped = dropped + [
            {"region": report.drop_region, "reason": report.drop_reason}
        ]
        logger.info(
            "trip.check_constraints | RE-PLAN dropping=%s | replan_count %d→%d",
            report.drop_region, replan_count, replan_count + 1,
        )
        return {
            "dropped_regions": new_dropped,
            "replan_count": replan_count + 1,
            "pending_replan": True,
            # Carry the flag forward so a co-occurring infeasibility survives the
            # re-plan and reaches the final reply.
            "infeasible": infeasible,
        }

    # Final pass: augment the reply with any conflict prose, record the flag.
    dropped_set = {d["region"] for d in dropped}
    kept = [r for r in requested if r not in dropped_set]
    base_reply = state.get("reply") or ""
    final_reply = augment_reply(
        base_reply, slots, requested, kept, dropped, infeasible
    )
    logger.info(
        "trip.check_constraints | final | dropped=%d | infeasible=%s | over=%s",
        len(dropped), bool(infeasible), report.over_constrained,
    )
    return {
        "reply": final_reply,
        "infeasible": infeasible,
        "pending_replan": False,
    }


def _route_after_check(state: TripState) -> str:
    """Conditional edge off ``check_constraints``: back to ``plan`` or end the turn.

    Takes the back-edge into ``plan`` when a corrective re-plan was requested
    (``pending_replan``), else ends. The re-plan loop is bounded by ``replan_count``
    in ``detect_conflicts`` (``_MAX_REPLANS``), so this can never spin.
    """
    return "plan" if state.get("pending_replan") else "end"


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

    Shape: ``START → gather → {elicit → END | plan → check_constraints →
    {plan | END}}``. ``gather`` extracts + merges slots; a conditional edge routes to
    ``elicit`` (missing required slot) or the discrete ``plan`` node (all required
    present). ``plan → check_constraints`` then applies the PR7 haversine conflict
    rules, with a conditional back-edge into ``plan`` for a bounded corrective
    re-plan. Compiled WITH a checkpointer so ``TripSlots`` (+ the PR7 re-plan fields)
    are retained per ``thread_id`` across ``ainvoke`` calls — the multi-turn
    elicit-loop.
    """
    builder = StateGraph(TripState)
    builder.add_node("gather", _gather_node)
    builder.add_node("elicit", _elicit_node)
    builder.add_node("plan", _plan_node)
    builder.add_node("check_constraints", _check_constraints_node)
    builder.add_edge(START, "gather")
    builder.add_conditional_edges(
        "gather", _route_after_gather, {"elicit": "elicit", "plan": "plan"}
    )
    builder.add_edge("elicit", END)
    # PR7: plan → check_constraints, with a conditional back-edge into plan for a
    # bounded corrective re-plan (drop the farthest outlier). Additive: the plan
    # node is unchanged in identity; only its outgoing edge moved from END to the
    # new node (re-planning-readiness property #3).
    builder.add_edge("plan", "check_constraints")
    builder.add_conditional_edges(
        "check_constraints", _route_after_check, {"plan": "plan", "end": END}
    )
    return builder.compile(checkpointer=_build_checkpointer())


# Module-level singleton compiled once at import. This keeps the in-process
# MemorySaver alive for the whole process, so slots accumulate across /chat
# requests keyed by thread_id=session_id.
trip_graph = build_trip_graph()
