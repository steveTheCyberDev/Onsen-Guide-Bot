"""Trip-planner agent working state — the LangGraph ``StateGraph`` schema.

This is the **agent working state** the LangGraph checkpointer persists per
``thread_id = session_id`` (docs/v3-trip-planner-plan.md §0/§4). It is distinct
from the Step-0 session store (``services/chat/chat_service.py``), which holds the
raw conversation *transcript*: this schema holds the *structured* state the agent
accumulates while planning (slots, candidates, itinerary).

PR3a intentionally defined the fields the later slices FILL, so 3b/3c ADD logic to
existing fields rather than introducing a new state concept (re-planning-readiness
property #2 in §0):

  * ``slots``      — PR3b fills this with a serialised ``TripSlots``
                     (``model_dump()``), merged + accumulated turn over turn. Stored
                     as a plain dict (not the model) so the checkpointed state stays
                     JSON-serialisable for the future PostgresSaver.
  * ``candidates`` — onsen candidates the plan node retrieves per region via
                     ``query_onsen_structured`` (PR3c fills this).
  * ``itinerary``  — the naive assembled plan the plan node produces (PR3c fills
                     this): ``{nights, regions: [...], selected_onsens: [...]}``.

``turn_count`` is the 3a proof-of-life for the checkpointer: the stub node reads
the value the previous turn left behind and increments it, so a second invoke on
the same ``thread_id`` observes accumulated state (the whole point of 3a).
"""

from typing import Any, TypedDict


class TripState(TypedDict, total=False):
    """Accumulating working state for the trip-planner graph.

    ``total=False`` so nodes may return partial updates and early turns need not
    populate the forward-compatible placeholder fields. The default channel
    behaviour is last-write-wins (replace); no custom reducers are needed in 3a —
    the stub node reads ``turn_count`` from the loaded checkpoint and returns the
    incremented value explicitly, which is version-robust across LangGraph
    releases and makes the "state survived across turns" assertion unambiguous.
    """

    # --- per-turn IO -------------------------------------------------------
    message: str  # latest user message, supplied fresh as input each turn
    reply: str  # node output surfaced back to plan_trip -> AgentResponse.reply

    # --- accumulating working state ---------------------------------------
    # 3a proof that checkpointed state persists+accumulates across turns of the
    # same thread_id. Later slices keep it as a cheap turn counter.
    turn_count: int

    # --- accumulating + forward-compatible fields --------------------------
    # PR3b: a serialised TripSlots (model_dump()) merged across turns. Kept as a
    # dict (not the model) so the checkpoint is JSON-serialisable for PostgresSaver.
    slots: dict[str, Any] | None
    # PR3c fills these from query_onsen_structured + itinerary assembly.
    candidates: list[Any]
    itinerary: dict[str, Any] | None

    # --- PR7 re-planning fields (haversine routing + constraints) -------------
    # Added as new FIELDS (not a new state concept), per re-planning-readiness
    # property #2: the check_constraints node reads/writes these and the plan node
    # consumes ``dropped_regions``. All JSON-serialisable for the future PostgresSaver.
    #
    # Regions removed by the over-constrained corrective rule, each
    # ``{region, reason}``. The plan node excludes these on a re-plan pass; the reply
    # explains them. Empty on a normal (non-re-planned) trip.
    dropped_regions: list[dict[str, Any]]
    # Advisory geographic-infeasibility flag {regions: [a, b], leg_km: float} or None
    # — set when two regions are too far apart to be one land trip. Never triggers a
    # re-plan (can't be fixed by dropping); only annotates the reply.
    infeasible: dict[str, Any] | None
    # How many corrective re-plans have run — bounds the check_constraints back-edge.
    replan_count: int
    # Transient router signal: True when check_constraints wants the plan back-edge.
    # Read by the conditional edge, reset to False on the final (non-re-plan) pass.
    pending_replan: bool

    # --- pc1 trip-analyze (grounded recommendation) ---------------------------
    # Added as a new FIELD (not a new state concept), same additive discipline as the
    # PR7 fields above. The analyze node (gated by ``analyze_enabled``) runs ONCE on
    # the settled itinerary and writes the top-level grounded recommendation here
    # ("why this itinerary suits you"). None when the gate is off or there were no
    # stops. Per-stop pros/cons are attached onto ``itinerary['selected_onsens']``
    # (carried through to OnsenResult by ``onsen_results_from_itinerary``). The
    # ``agent.py`` boundary projects both onto ``AgentResponse``. JSON-serialisable
    # for the future PostgresSaver.
    recommendation: str | None
