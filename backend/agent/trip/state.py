"""Trip-planner agent working state — the LangGraph ``StateGraph`` schema.

This is the **agent working state** the LangGraph checkpointer persists per
``thread_id = session_id`` (docs/v3-trip-planner-plan.md §0/§4). It is distinct
from the Step-0 session store (``services/chat/chat_service.py``), which holds the
raw conversation *transcript*: this schema holds the *structured* state the agent
accumulates while planning (slots, candidates, itinerary).

PR3a intentionally defines the fields the later slices FILL, so 3b/3c ADD logic to
existing fields rather than introducing a new state concept (re-planning-readiness
property #2 in §0):

  * ``slots``      — placeholder for the ``TripSlots`` model that lands in PR3b.
                     A plain dict/None is fine for 3a (no elicitation yet).
  * ``candidates`` — placeholder for onsen candidates that PR3c fills from
                     ``query_onsen_structured``.
  * ``itinerary``  — placeholder for the assembled plan that PR3c produces.

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

    # --- forward-compatible placeholders (filled by later slices) ----------
    # PR3b replaces `dict | None` with the real TripSlots model; 3a leaves it None.
    slots: dict[str, Any] | None
    # PR3c fills these from query_onsen_structured + itinerary assembly.
    candidates: list[Any]
    itinerary: dict[str, Any] | None
