"""Naive itinerary assembly for the trip-planner ``plan`` node (V3 PR3c).

This is the deterministic data + assembly layer the discrete ``plan`` node runs
once every required slot is present. It mirrors the **deterministic-assembly
discipline** of the ``search`` mode (``agent/workflow/pipeline.py``) and §5 of
docs/v3-trip-planner-plan.md: the itinerary is built from REAL retrieved onsen
records — never LLM-generated, never fabricated — so there is NO LLM call in the
plan path. The only judgement is a simple Python distribution of ``nights`` across
``regions`` and a top-N pick of onsen per region.

Two-tier flow (mirrors the workflow's data-layer/assembly split):

  1. ``retrieve_candidates`` — the FIRST ``services/`` call from ``agent/trip/``.
     Reuses the SAME retrieval the ``search``/``recommend`` modes use
     (``query_onsen_structured`` in ``services/retrieval/retrieval_service.py``),
     filtered per region/prefecture in ``slots.regions``. This is allowed: the
     live workflow engine calls ``services/`` directly; the layering rule only
     forbids ``agent/ → api/``.
  2. ``build_itinerary`` — pure Python. Distributes ``nights`` across ``regions``
     (~1 onsen-stop per night for the default ``relaxed`` pace, 2 for ``packed``),
     picks the top candidates per region, and records regions with NO ingested
     data explicitly so the reply can say "no onsen found for X" instead of
     inventing stops.

Everything here stays JSON-serialisable (plain dicts/lists) so the produced
``itinerary`` and ``candidates`` can be checkpointed for the future PostgresSaver.
Projection onto the Pydantic ``OnsenResult`` happens at the ``agent/trip/agent.py``
boundary via :func:`onsen_results_from_itinerary`, keeping the graph state plain.

Layering: imports ``agent/`` schemas + now ``services/`` retrieval — never ``api/``.
PR3c adds NO re-planning, constraint checks, hotels, routing, or Places (PR5/6/7).
"""

import logging

from agent.agent import OnsenResult
from services.retrieval.retrieval_service import query_onsen_structured

logger = logging.getLogger(__name__)

# How many candidate onsen to pull per region before selecting stops. Mirrors the
# workflow's ``_MAX_RESULTS`` ceiling so trip retrieval sees the same candidate
# pool depth as search/recommend for a single prefecture.
_CANDIDATES_PER_REGION = 20

# Onsen-stops per night by pace (§2): relaxed ≈ 1 stop/night, packed ≈ 2. Named
# constant with all allowed values so the pace→density mapping is explicit and the
# default (relaxed) is unambiguous. Any unknown pace falls back to relaxed.
_STOPS_PER_NIGHT: dict[str, int] = {"relaxed": 1, "packed": 2}
_DEFAULT_STOPS_PER_NIGHT = 1  # relaxed

# Keys on a query_onsen_structured record that OnsenResult accepts. The records
# carry EXTRA keys (detail_url) that are NOT OnsenResult fields; OnsenResult forbids
# extras (Pydantic v2 default), so we project onto this allow-list rather than
# ``OnsenResult(**record)``. Mirrors ``pipeline._ONSEN_FIELDS`` — kept local so
# agent/trip/ does not couple to agent/workflow internals.
_ONSEN_FIELDS = ("name", "location", "spring_type", "spa_quality", "lat", "lng")


def _stops_per_night(pace: str) -> int:
    """Onsen-stops per night for a pace, defaulting to relaxed (1) if unknown."""
    return _STOPS_PER_NIGHT.get(pace, _DEFAULT_STOPS_PER_NIGHT)


def allocate_nights(nights: int, num_regions: int) -> list[int]:
    """Distribute ``nights`` across ``num_regions`` as evenly as possible.

    Front-loads the remainder so earlier regions absorb the extra night(s), e.g.
    5 nights over 2 regions → ``[3, 2]``; 4 over 3 → ``[2, 1, 1]``. When there are
    more regions than nights, later regions get 0 allocated nights (they still earn
    a single day-trip onsen stop in :func:`build_itinerary` via ``max(1, ...)``).
    Guards ``num_regions == 0`` (no regions) with an empty allocation.
    """
    if num_regions <= 0:
        return []
    nights = max(0, nights)
    base, remainder = divmod(nights, num_regions)
    return [base + (1 if i < remainder else 0) for i in range(num_regions)]


def retrieve_candidates(slots: dict) -> dict[str, list[dict]]:
    """Retrieve onsen candidates per region via ``query_onsen_structured``.

    The FIRST ``services/`` call from ``agent/trip/`` — reuses the exact retrieval
    the search/recommend modes use, once per region, filtered to that region's
    prefecture. The free-text ``spring_or_scenery_prefs`` slot (default "") is the
    semantic query; the empty case is handled by the service's own neutral fallback.

    Args:
        slots: The serialised ``TripSlots`` dict from graph state.

    Returns:
        ``{region -> list of structured onsen records}`` in ``slots['regions']``
        order. A region with no ingested data maps to an empty list (never
        fabricated). Records are the plain dicts ``query_onsen_structured`` returns.
    """
    query = slots.get("spring_or_scenery_prefs") or ""
    by_region: dict[str, list[dict]] = {}
    for region in slots.get("regions", []):
        records = query_onsen_structured(
            query, prefecture=region, n_results=_CANDIDATES_PER_REGION
        )
        by_region[region] = records
        logger.info(
            "trip.retrieve_candidates | region=%s | candidates=%d", region, len(records)
        )
    return by_region


def build_itinerary(slots: dict, candidates: dict[str, list[dict]]) -> dict:
    """Assemble a naive itinerary deterministically (no LLM).

    Distributes ``nights`` across ``regions`` and picks the top onsen per region
    (~``pace`` stops per allocated night, min 1 per region that HAS data). Regions
    with no retrieved candidates are flagged ``no_data=True`` and contribute NO
    onsen — the grounding rule (§2): real, in-region onsen only, never invented.

    Args:
        slots: The serialised ``TripSlots`` dict (needs ``regions``, ``nights``,
            ``pace``).
        candidates: ``{region -> records}`` from :func:`retrieve_candidates`.

    Returns:
        A JSON-serialisable itinerary dict:
          - ``nights``: total trip nights.
          - ``regions``: per-region legs, each
            ``{region, nights, no_data, onsens: [record, ...]}`` in request order.
          - ``selected_onsens``: the flat list of chosen onsen records, in
            itinerary order — the source for ``AgentResponse.onsens``.
    """
    regions = slots.get("regions", [])
    nights = slots.get("nights") or 0
    stops_per_night = _stops_per_night(slots.get("pace", "relaxed"))
    allocation = allocate_nights(nights, len(regions))

    legs: list[dict] = []
    selected: list[dict] = []
    for region, region_nights in zip(regions, allocation):
        pool = candidates.get(region, [])
        if not pool:
            # No ingested data for this region → say so explicitly, never invent.
            legs.append(
                {"region": region, "nights": region_nights, "no_data": True, "onsens": []}
            )
            logger.warning("trip.build_itinerary | no onsen data for region=%s", region)
            continue
        # At least one stop even for a 0-night region (day trip); otherwise ~pace
        # stops per allocated night, capped by how many candidates actually exist.
        want = max(1, region_nights * stops_per_night)
        picked = pool[:want]
        legs.append(
            {"region": region, "nights": region_nights, "no_data": False, "onsens": picked}
        )
        selected.extend(picked)

    return {"nights": nights, "regions": legs, "selected_onsens": selected}


def build_reply(itinerary: dict) -> str:
    """Render the deterministic template reply describing the itinerary.

    No LLM: a plain description built from the assembled legs. Names real onsen per
    region and states "No onsen found for X" for any region with no ingested data,
    so the prose never implies stops that do not exist.
    """
    nights = itinerary.get("nights", 0)
    legs = itinerary.get("regions", [])
    planned = [leg for leg in legs if not leg["no_data"]]
    missing = [leg for leg in legs if leg["no_data"]]

    # Every requested region lacked data → be explicit, plan nothing.
    if not planned:
        names = ", ".join(leg["region"] for leg in legs)
        return (
            f"I couldn't find any onsen in our data for the requested area(s): "
            f"{names}. Try a different prefecture and I'll build an itinerary."
        )

    segments = []
    for leg in planned:
        night_word = "night" if leg["nights"] == 1 else "nights"
        onsen_names = ", ".join(o["name"] for o in leg["onsens"])
        segments.append(f"{leg['region']} ({leg['nights']} {night_word}): {onsen_names}")

    night_word = "night" if nights == 1 else "nights"
    reply = (
        f"Here's a naive {nights}-{night_word} onsen itinerary — "
        + "; ".join(segments)
        + "."
    )
    if missing:
        reply += " " + "; ".join(
            f"No onsen found for {leg['region']}" for leg in missing
        ) + "."
    return reply


def onsen_results_from_itinerary(itinerary: dict) -> list[OnsenResult]:
    """Project the itinerary's selected onsen records onto ``OnsenResult``.

    Called at the ``agent.py`` boundary to populate ``AgentResponse.onsens``.
    Records carry extra keys (detail_url) OnsenResult forbids, so we pass only the
    allow-listed fields. Kept here so the raw records stay in the (JSON-serialisable)
    checkpointed state and the Pydantic projection lives in one place.
    """
    selected = itinerary.get("selected_onsens", [])
    return [OnsenResult(**{k: r.get(k) for k in _ONSEN_FIELDS}) for r in selected]


def assemble_trip(slots: dict) -> dict:
    """Retrieve candidates and assemble the naive itinerary for the plan node.

    Orchestrates the two tiers: :func:`retrieve_candidates` (services call) then
    :func:`build_itinerary` + :func:`build_reply` (pure Python). Returns the pieces
    the ``plan`` node writes back into graph state.

    Args:
        slots: The serialised ``TripSlots`` dict from graph state (required slots
            already present — enforced by the elicit gate upstream).

    Returns:
        ``{candidates, itinerary, reply}`` where ``candidates`` is the flat list of
        every retrieved record (what the plan considered), ``itinerary`` is the
        assembled plan dict, and ``reply`` is the template prose.
    """
    by_region = retrieve_candidates(slots)
    itinerary = build_itinerary(slots, by_region)
    reply = build_reply(itinerary)
    flat_candidates = [rec for records in by_region.values() for rec in records]
    logger.info(
        "trip.assemble_trip | regions=%d | candidates=%d | selected=%d",
        len(itinerary["regions"]),
        len(flat_candidates),
        len(itinerary["selected_onsens"]),
    )
    return {"candidates": flat_candidates, "itinerary": itinerary, "reply": reply}
