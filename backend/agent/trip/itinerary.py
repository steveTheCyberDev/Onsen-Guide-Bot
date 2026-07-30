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

PR5 adds a HOTEL step: for each selected onsen stop with coordinates, look up
nearby lodging via the existing ``search_hotels`` (Rakuten), attaching the result
to that stop. It follows the workflow's **fail-soft** discipline — a Rakuten
outage/error never breaks the response; that stop simply has no hotels and the
reply says so explicitly ("no hotels found nearby"), never fabricated.

Everything here stays JSON-serialisable (plain dicts/lists) so the produced
``itinerary`` and ``candidates`` can be checkpointed for the future PostgresSaver.
Projection onto the Pydantic ``OnsenResult`` / ``HotelResult`` happens at the
``agent/trip/agent.py`` boundary via :func:`onsen_results_from_itinerary` /
:func:`hotel_results_from_itinerary`, keeping the graph state plain.

Layering: imports ``agent/`` schemas + ``services/`` (retrieval + rakuten) — never
``api/``. PR5 adds hotels only; NO routing, Places, weather, or re-planning (PR6/7).
"""

import logging

from agent.schemas import HotelResult, OnsenResult
from services.rakuten.rakuten_service import search_hotels
from services.retrieval.retrieval_service import query_onsen_structured
from services.routing.routing_service import (
    centroid,
    order_nearest_neighbour,
    route_legs as _routing_legs,
)

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
# carry EXTRA keys (detail_url, and now hotels) that are NOT OnsenResult fields;
# OnsenResult forbids extras (Pydantic v2 default), so we project onto this
# allow-list rather than ``OnsenResult(**record)``. Mirrors ``pipeline._ONSEN_FIELDS``
# — kept local so agent/trip/ does not couple to agent/workflow internals.
_ONSEN_FIELDS = ("name", "location", "spring_type", "spa_quality", "lat", "lng")

# How many hotel names to name per onsen stop in the deterministic reply. The full
# hotel set is always carried in state + AgentResponse.hotels; this only bounds the
# prose so a busy leg stays readable.
_HOTELS_IN_REPLY = 3


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


def region_centroids(candidates: dict[str, list[dict]]) -> dict[str, list[float]]:
    """Compute each region's centroid ``[lat, lng]`` from its candidate coordinates.

    The deterministic haversine routing/constraint layer (PR7) needs a single point
    per region to measure inter-region separation. We take the mean of the region's
    retrieved onsen coordinates (records without coords are skipped). A region with
    no geocoded candidates maps to no entry (it can't participate in distance rules).
    Stored as plain ``[lat, lng]`` lists so the itinerary stays JSON-serialisable for
    the checkpointer.
    """
    out: dict[str, list[float]] = {}
    for region, records in candidates.items():
        points = [
            (r["lat"], r["lng"])
            for r in records
            if r.get("lat") is not None and r.get("lng") is not None
        ]
        c = centroid(points)
        if c is not None:
            out[region] = [c[0], c[1]]
    return out


def build_route_legs(selected: list[dict]) -> list[dict]:
    """Order the selected onsen stops nearest-neighbour and return their legs.

    A tiny deterministic TSP-ish pass (:func:`order_nearest_neighbour`) over the
    stops' ingest-time coordinates produces sensible legs (``{from, to,
    distance_km}``) instead of the raw retrieval order. Stops without coordinates are
    skipped (they can't be routed). Returns ``[]`` when fewer than two stops have
    coords. Names must be unique enough to label a leg; onsen names serve here.
    """
    stops = [
        (o.get("name", ""), o["lat"], o["lng"])
        for o in selected
        if o.get("lat") is not None and o.get("lng") is not None
    ]
    if len(stops) < 2:
        return []
    by_name = {s[0]: s for s in stops}
    ordered_labels = order_nearest_neighbour(stops)
    ordered = [by_name[label] for label in ordered_labels]
    return _routing_legs(ordered)


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
          - ``region_centroids``: ``{region: [lat, lng]}`` mean coordinates per
            region (PR7 routing; regions without coords omitted).
          - ``route_legs``: nearest-neighbour-ordered legs between the selected
            stops (``[{from, to, distance_km}, ...]``; PR7 routing).
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

    return {
        "nights": nights,
        "regions": legs,
        "selected_onsens": selected,
        # PR7 routing signals (deterministic haversine over ingest-time coords):
        # region_centroids feeds the constraint rules; route_legs is the NN-ordered
        # stop route. Both plain lists/dicts so the itinerary stays checkpointer-safe.
        "region_centroids": region_centroids(candidates),
        "route_legs": build_route_legs(selected),
    }


def _to_hotel(h: dict) -> HotelResult:
    """Map a Rakuten service hotel dict to a HotelResult.

    Mirrors ``pipeline._to_hotel`` field-for-field so trip and search/recommend
    produce identical hotel shapes. Rakuten returns Japanese-only names (no
    translation in V1), so name == originalName == the Japanese string. Kept local
    so agent/trip/ does not couple to agent/workflow internals.
    """
    name = h.get("name") or ""
    price = h.get("price")
    return HotelResult(
        name=name,
        originalName=name,
        location=h.get("address"),
        hotelSpecial=h.get("hotelSpecial"),
        price=str(price) if price is not None else None,
        image=h.get("hotelImageUrl"),
        url=h.get("url"),
        lat=h.get("lat"),
        lng=h.get("lng"),
    )


def _fetch_hotels_for_stop(onsen: dict) -> list[dict]:
    """Look up nearby hotels for one onsen stop — fail-soft, never raising.

    Uses the onsen's ingest-time coordinates. Mirrors the workflow's hotel step
    discipline (``agent/workflow/pipeline.py``): ``search_hotels`` is sync (uses
    ``requests``), and a Rakuten outage/error must NEVER break the plan — on any
    error (or missing coords) this returns ``[]`` and the stop is treated as
    "no hotels found nearby", never fabricated.

    Off the event loop: this runs inside the SYNC ``plan`` node, which LangGraph
    executes in its executor under ``ainvoke``, so the blocking Rakuten call does
    not block the loop (same trade-off the plan node makes for Chroma retrieval).
    """
    lat = onsen.get("lat")
    lng = onsen.get("lng")
    if lat is None or lng is None:
        logger.warning(
            "trip.hotels | onsen=%s has no coords — skipping hotel lookup",
            onsen.get("name"),
        )
        return []
    try:
        hotels = search_hotels(lat, lng)
        logger.info(
            "trip.hotels | onsen=%s | hotels=%d (lat=%s lng=%s)",
            onsen.get("name"), len(hotels), lat, lng,
        )
        return hotels
    except Exception as e:
        # Fail soft — leave this stop's hotels empty; NEVER 500 the whole plan.
        logger.warning(
            "trip.hotels | hotel lookup failed for onsen=%s (lat=%s lng=%s): %s",
            onsen.get("name"), lat, lng, e,
        )
        return []


def attach_hotels(itinerary: dict) -> None:
    """Attach nearby hotels to every selected onsen stop (in place, fail-soft).

    For each planned leg's onsen stop, fetch nearby lodging and set
    ``onsen['hotels']`` to the (possibly empty) list of raw hotel dicts. Mutates the
    itinerary in place; the leg onsen and ``selected_onsens`` share the same dict
    refs, so both see the attached hotels. No-data legs carry no onsen, so they get
    no hotels. Stays JSON-serialisable (raw hotel dicts) for the checkpointer.
    """
    for leg in itinerary.get("regions", []):
        for onsen in leg.get("onsens", []):
            onsen["hotels"] = _fetch_hotels_for_stop(onsen)


def hotel_results_from_itinerary(itinerary: dict) -> list[HotelResult]:
    """Flatten every stop's hotels onto ``HotelResult``, de-duplicated.

    Called at the ``agent.py`` boundary to populate ``AgentResponse.hotels``.
    Onsen stops in the same leg can be close enough to return overlapping hotels,
    so we de-dupe by ``(name, url)`` to avoid the same hotel appearing many times.
    Every surfaced hotel comes from a real per-stop ``search_hotels`` lookup — never
    fabricated.
    """
    seen: set[tuple] = set()
    results: list[HotelResult] = []
    for leg in itinerary.get("regions", []):
        for onsen in leg.get("onsens", []):
            for h in onsen.get("hotels") or []:
                key = (h.get("name"), h.get("url"))
                if key in seen:
                    continue
                seen.add(key)
                results.append(_to_hotel(h))
    return results


def build_reply(itinerary: dict) -> str:
    """Render the deterministic template reply describing the itinerary.

    No LLM: a plain description built from the assembled legs. Names real onsen per
    region (with their nearby hotels, or an explicit "no hotels found nearby" when a
    stop's lookup came back empty) and states "No onsen found for X" for any region
    with no ingested data — so the prose never implies onsen or hotels that do not
    exist. Hotel prose is only added for stops that went through the hotel step
    (an ``onsen['hotels']`` key present), so callers rendering an onsen-only
    itinerary are unaffected.
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
        onsen_segs = []
        for o in leg["onsens"]:
            seg = o["name"]
            hotels = o.get("hotels")
            # Only mention lodging for stops that went through the hotel step (key
            # present). A present-but-empty list means "we looked, found none".
            if hotels is not None:
                if hotels:
                    names = ", ".join(
                        h.get("name", "") for h in hotels[:_HOTELS_IN_REPLY]
                    )
                    seg += f" (nearby hotels: {names})"
                else:
                    seg += " (no hotels found nearby)"
            onsen_segs.append(seg)
        segments.append(
            f"{leg['region']} ({leg['nights']} {night_word}): " + "; ".join(onsen_segs)
        )

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

    Called at the ``agent.py`` boundary to populate ``AgentResponse.onsens`` (and
    inside the pc1 analyze node to build the candidates it judges). Records carry
    extra keys (detail_url, hotels) OnsenResult forbids, so we pass only the
    allow-listed fields. When the pc1 analyze node has run it attaches grounded
    ``pros``/``cons`` onto the record; those ARE OnsenResult fields, so we carry them
    through here (defaulting to ``[]`` when analyze didn't run — search/elicit paths
    are byte-identical). Kept here so the raw records stay in the (JSON-serialisable)
    checkpointed state and the Pydantic projection lives in one place.
    """
    selected = itinerary.get("selected_onsens", [])
    results: list[OnsenResult] = []
    for r in selected:
        data = {k: r.get(k) for k in _ONSEN_FIELDS}
        data["pros"] = r.get("pros") or []
        data["cons"] = r.get("cons") or []
        results.append(OnsenResult(**data))
    return results


def assemble_trip(slots: dict) -> dict:
    """Retrieve candidates and assemble the naive itinerary for the plan node.

    Orchestrates the tiers: :func:`retrieve_candidates` (Chroma) → :func:`build_itinerary`
    (pure Python) → :func:`attach_hotels` (Rakuten, fail-soft) → :func:`build_reply`.
    Returns the pieces the ``plan`` node writes back into graph state.

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
    # PR5: look up nearby hotels for each selected onsen stop (fail-soft, in place)
    # BEFORE building the reply so the prose can reference lodging.
    attach_hotels(itinerary)
    reply = build_reply(itinerary)
    flat_candidates = [rec for records in by_region.values() for rec in records]
    logger.info(
        "trip.assemble_trip | regions=%d | candidates=%d | selected=%d",
        len(itinerary["regions"]),
        len(flat_candidates),
        len(itinerary["selected_onsens"]),
    )
    return {"candidates": flat_candidates, "itinerary": itinerary, "reply": reply}
