"""Haversine great-circle routing — the deterministic distance seam (V3 PR7).

A framework-agnostic ``services/`` module (no LangChain, no ``agent/``/``api/``
imports) computing straight-line distances between onsen stops from their
ingest-time ``lat``/``lng`` coordinates. This is the ZERO-billing routing backend:
haversine on coordinates already stored in Chroma, so the trip-planner can order
stops and detect geographic conflicts WITHOUT calling any Google API.

Swappable-backend seam (deferred, DO NOT wire now — behind a billing STOP):
    The future upgrade is Google's **Distance Matrix / Directions** API, which
    returns real road/rail *travel time* (haversine underestimates both distance
    and time — it ignores mountains, coastlines, and the rail network). When that
    lands, add a ``services/routing`` function that queries Distance Matrix and
    have the trip-planner select the backend via a ``core.config`` setting (mirror
    ``trip_checkpointer_backend``). The call sites here (``distance_km`` and the
    ordering/leg helpers) are the seam: keep their signatures so only the distance
    source changes, not the callers. Until then, everything is haversine.

Pure functions over ``(lat, lng)`` floats; callers pass ``None``-free coordinates
(records without coords are filtered upstream). Everything returns plain
floats/lists/dicts so results stay JSON-serialisable for the LangGraph checkpointer.
"""

import math

# Mean Earth radius in kilometres (IUGG). Named constant with source so the unit
# is explicit and the haversine result is in km.
_EARTH_RADIUS_KM = 6371.0088

# Decimal places leg distances are rounded to. km precision is meaningless below
# 1 dp for straight-line estimates; named (mirrors _EARTH_RADIUS_KM) rather than an
# inline magic literal.
_LEG_KM_PRECISION_DP = 1


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometres between two lat/lng points.

    The standard haversine formula on a spherical Earth (``_EARTH_RADIUS_KM``).
    Straight-line ("as the crow flies") distance — it does NOT account for roads,
    rail, terrain, or water crossings; that is the Distance Matrix upgrade noted in
    the module docstring. Good enough for ordering stops and separating a
    same-region hop from a needs-a-flight leg.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance in km between two ``(lat, lng)`` points — the backend seam.

    A thin wrapper over :func:`haversine_km` taking ``(lat, lng)`` tuples. This is
    the single call site a future Distance Matrix backend would swap: keep this
    signature and only the distance source changes, not the ordering/conflict
    callers below.
    """
    return haversine_km(a[0], a[1], b[0], b[1])


def centroid(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Mean ``(lat, lng)`` of a set of points, or ``None`` if empty.

    A coarse "where is this region" summary — the arithmetic mean of the member
    onsen coordinates. Good enough at country scale to measure inter-region
    separation; it is NOT a geodesic centroid (fine for the ~hundreds-of-km
    thresholds the trip-planner uses).
    """
    if not points:
        return None
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def order_nearest_neighbour(
    stops: list[tuple[str, float, float]],
) -> list[str]:
    """Greedy nearest-neighbour ordering of labelled ``(label, lat, lng)`` stops.

    A tiny deterministic TSP-ish heuristic: start at the FIRST stop (preserving the
    caller's intended entry point / itinerary order) and repeatedly hop to the
    nearest not-yet-visited stop by :func:`distance_km`. Returns the labels in visit
    order. Ties break on input order (``min`` is stable), so the result is fully
    deterministic. 0/1 stops return their labels unchanged.
    """
    if len(stops) <= 1:
        return [s[0] for s in stops]
    remaining = list(stops)
    ordered = [remaining.pop(0)]
    while remaining:
        current_stop = ordered[-1]
        nearest_index = min(
            range(len(remaining)),
            key=lambda i: distance_km(
                (current_stop[1], current_stop[2]),
                (remaining[i][1], remaining[i][2]),
            ),
        )
        ordered.append(remaining.pop(nearest_index))
    return [s[0] for s in ordered]


def route_legs(
    ordered_stops: list[tuple[str, float, float]],
) -> list[dict]:
    """Consecutive legs of an ORDERED stop list, each with a haversine distance.

    Given stops already in visit order (e.g. from :func:`order_nearest_neighbour`),
    returns ``[{from, to, distance_km}, ...]`` — one leg per adjacent pair. Empty
    for 0/1 stops. ``distance_km`` is rounded to ``_LEG_KM_PRECISION_DP`` (km
    precision is meaningless below that for straight-line estimates).
    """
    legs: list[dict] = []
    for a, b in zip(ordered_stops, ordered_stops[1:]):
        legs.append(
            {
                "from": a[0],
                "to": b[0],
                "distance_km": round(
                    distance_km((a[1], a[2]), (b[1], b[2])), _LEG_KM_PRECISION_DP
                ),
            }
        )
    return legs


def max_pairwise_km(centroids: dict[str, tuple[float, float]]) -> float:
    """Largest straight-line distance between any two of the given centroids.

    The "spread" of a multi-region trip: 0.0 for 0/1 regions. Used by the
    trip-planner to tell an in-reach cluster (all legs short) from a leg that needs
    a flight / crosses water (one very long pair).
    """
    labels = list(centroids)
    best = 0.0
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            d = distance_km(centroids[labels[i]], centroids[labels[j]])
            if d > best:
                best = d
    return best


def farthest_pair(centroids: dict[str, tuple[float, float]]) -> tuple[str, str] | None:
    """The two region labels that are farthest apart, or ``None`` for < 2 regions.

    Names the offending leg for a geographic-infeasibility message (e.g. the
    Okinawa↔Gifu pair). Deterministic: ties keep the first-seen pair in insertion
    order.
    """
    labels = list(centroids)
    if len(labels) < 2:
        return None
    best_pair: tuple[str, str] | None = None
    best = -1.0
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            d = distance_km(centroids[labels[i]], centroids[labels[j]])
            if d > best:
                best = d
                best_pair = (labels[i], labels[j])
    return best_pair


def farthest_outlier(centroids: dict[str, tuple[float, float]]) -> str | None:
    """The region whose TOTAL distance to all the others is greatest, or ``None``.

    The single most-dispersed region — the sensible one to drop when a trip spans
    too many regions for its nights. Sums each region's distance to every other and
    returns the max. Returns ``None`` for < 2 regions (nothing to be an outlier
    from). Deterministic: ties keep the first-seen label (insertion order).
    """
    labels = list(centroids)
    if len(labels) < 2:
        return None
    best_label: str | None = None
    best_total = -1.0
    for a in labels:
        total = sum(
            distance_km(centroids[a], centroids[b]) for b in labels if b != a
        )
        if total > best_total:
            best_total = total
            best_label = a
    return best_label
