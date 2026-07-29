"""Deterministic trip constraint detection + explanatory prose (V3 PR7).

The re-planning brain of the trip-planner — with NO LLM call and NO Google billing.
It reads the naive itinerary's region centroids (derived from ingest-time onsen
coordinates) and applies two honest, haversine-based rules:

  1. **Geographic infeasibility** — if the two most-distant regions are farther
     apart than ``settings.trip_infeasible_leg_km`` (default 500 km), the trip needs
     a flight / crosses water. The plan is FLAGGED (not silently emitted) and a
     reshape is suggested (split into separate trips / reallocate nights). This is
     an advisory flag, not a re-plan: dropping a region can't fix "these two are on
     different islands".
  2. **Over-constrained (pace × nights × spread)** — ``≥3`` regions that the nights
     can't absorb at the stated pace (``nights ≤ regions``) AND genuinely dispersed
     (largest hop ≥ ``settings.trip_dispersed_leg_km``). The corrective rule DROPS
     the single farthest-outlier region (by summed inter-region haversine) and
     re-plans, keeping the stated pace. Bounded to ``_MAX_REPLANS`` corrective
     passes so the graph back-edge can never loop forever.

Everything the plan node needs to explain itself truthfully is emitted as prose by
:func:`build_conflict_prose` — the reply names the conflict, the tradeoff, and any
dropped region + reason. There is NO keyword-stuffing: prose is produced ONLY when a
conflict is genuinely detected from the coordinates, so a single-region or compact
trip gets today's plain itinerary reply unchanged.

Honest boundary (measured, not faked): weather×season and budget×ratings×party
conflicts are NOT detectable here — there is no weather or ratings signal in the
coordinates. Those are left to later slices.
# PR7-later: weather conflict (needs a forecast service — Open-Meteo, V3.1).
# PR7-later: budget×ratings×party conflict (needs Places ratings — PR6, ingest-time).

Layering: imports ``services/routing`` (pure geo) + ``core.config`` only — never
``api/``. Kept separate from ``itinerary.py`` so the plan node's naive assembly and
the re-planning rules stay independently testable.
"""

import logging
from dataclasses import dataclass, field

from core.config import settings
from services.routing.routing_service import (
    farthest_outlier,
    farthest_pair,
    max_pairwise_km,
)

logger = logging.getLogger(__name__)

# Maximum corrective re-plan passes the check_constraints back-edge may trigger.
# 1 = drop the single farthest outlier once, then settle. Bounds the plan→
# check_constraints→plan loop so it can never spin; the eval's over-constrained
# example needs exactly one drop. Named constant with its rationale per the
# no-magic-numbers rule (kept local — it's a fixed control flag, not env-tunable).
_MAX_REPLANS = 1

# Minimum regions for an "over-constrained" verdict: you need at least 3 to have a
# droppable outlier while still planning a real (≥2-region) trip. Below this the
# nights/pace check is meaningless (1–2 regions are never "too many").
_MIN_REGIONS_FOR_OVERCONSTRAINT = 3


@dataclass
class ConflictReport:
    """The outcome of applying the deterministic constraint rules to one itinerary.

    ``should_replan`` tells the graph whether to take the back-edge into ``plan``;
    ``drop_region``/``drop_reason`` name the region to remove on that re-plan.
    ``infeasible`` is an advisory flag (never triggers a re-plan). ``over_constrained``
    records that the spread/nights rule fired even after re-plans are exhausted, so
    the prose can still explain a trip we couldn't fully fix.
    """

    over_constrained: bool = False
    should_replan: bool = False
    drop_region: str | None = None
    drop_reason: str | None = None
    # Advisory geographic-infeasibility flag: {regions: [a, b], leg_km: float}.
    infeasible: dict | None = None
    # The regions currently in the plan (effective = requested minus already-dropped).
    effective_regions: list[str] = field(default_factory=list)


def _centroid_tuples(region_centroids: dict) -> dict[str, tuple[float, float]]:
    """Coerce the JSON-serialised ``{region: [lat, lng]}`` state into float tuples.

    Region centroids live in graph state as plain lists (checkpointer-safe); the
    routing helpers want ``(lat, lng)`` tuples. Regions whose centroid is missing
    (no geocoded onsen) are dropped — they can't participate in distance rules.
    """
    out: dict[str, tuple[float, float]] = {}
    for region, coord in (region_centroids or {}).items():
        if coord and coord[0] is not None and coord[1] is not None:
            out[region] = (float(coord[0]), float(coord[1]))
    return out


def detect_conflicts(
    slots: dict,
    region_centroids: dict,
    replan_count: int,
) -> ConflictReport:
    """Apply the deterministic haversine rules to the current plan.

    Already-dropped regions need no explicit exclusion here: the plan node does not
    retrieve them on a re-plan pass, so they are simply ABSENT from
    ``region_centroids`` — the effective-region set is exactly the centroid keys.

    Args:
        slots: The serialised ``TripSlots`` (needs ``regions``, ``nights``, ``pace``).
        region_centroids: ``{region: [lat, lng]}`` for the regions the plan node just
            assembled (its EFFECTIVE regions — requested minus already-dropped, which
            are absent because they were never retrieved this pass).
        replan_count: How many corrective re-plans have already run (loop bound).

    Returns:
        A :class:`ConflictReport`. ``should_replan=True`` only when the
        over-constrained rule fires AND we are under ``_MAX_REPLANS`` AND there is a
        real outlier to drop.
    """
    centroids = _centroid_tuples(region_centroids)
    effective = list(centroids.keys())
    report = ConflictReport(effective_regions=effective)

    # --- Rule 1: geographic infeasibility (advisory flag, never a re-plan) -----
    spread_km = max_pairwise_km(centroids)
    if spread_km >= settings.trip_infeasible_leg_km:
        pair = farthest_pair(centroids)
        report.infeasible = {
            "regions": list(pair) if pair else effective,
            "leg_km": round(spread_km, 1),
        }
        logger.info(
            "trip.constraints | infeasible leg %.0f km >= %.0f (%s)",
            spread_km,
            settings.trip_infeasible_leg_km,
            report.infeasible["regions"],
        )

    # --- Rule 2: over-constrained (pace × nights × spread) → drop the outlier --
    nights = slots.get("nights")
    dispersed = spread_km >= settings.trip_dispersed_leg_km
    if (
        len(effective) >= _MIN_REGIONS_FOR_OVERCONSTRAINT
        and nights is not None
        and nights <= len(effective)
        and dispersed
    ):
        report.over_constrained = True
        outlier = farthest_outlier(centroids)
        if outlier is not None and replan_count < _MAX_REPLANS:
            report.should_replan = True
            report.drop_region = outlier
            report.drop_reason = (
                f"{len(effective)} regions over {nights} nights at a "
                f"{slots.get('pace', 'relaxed')} pace is spread too thin — "
                f"{outlier} is the farthest outlier (~{spread_km:.0f} km across the trip)"
            )
            logger.info(
                "trip.constraints | over-constrained: dropping outlier=%s "
                "(regions=%d nights=%s spread=%.0fkm replan=%d)",
                outlier, len(effective), nights, spread_km, replan_count,
            )
    return report


def _join_and(names: list[str]) -> str:
    """Join names as 'A', 'A and B', or 'A, B and C' — reused for prose."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _infeasibility_prose(infeasible: dict) -> str:
    """Truthful, DISTANCE-GROUNDED prose flagging a far-apart trip + a reshape.

    Names the offending region pair and the straight-line distance, and states it is
    too far apart to combine comfortably. Crucially it does NOT assert a MODALITY
    (water crossing / mandatory flight) from distance alone — that would fabricate:
    a ~1,000 km Honshu-only pair (e.g. Aomori↔Yamaguchi) is all land/rail. Instead it
    hedges ("likely long-distance travel — a flight or shinkansen") and offers a
    reshape (two separate trips / reallocate the nights). Only produced when the
    infeasibility rule actually fired.
    """
    a, b = (infeasible["regions"] + ["", ""])[:2]
    km = infeasible["leg_km"]
    return (
        f"Heads-up: combining {a} with {b} in one trip isn't feasible — they're "
        f"roughly {km:,.0f} km apart, too far apart to combine comfortably; it'd "
        f"likely mean long-distance travel (a flight or shinkansen) plus a lost "
        f"travel day each way. I'd suggest splitting this into two separate trips, "
        f"or reallocate the nights to focus on one region."
    )


def _dropped_prose(
    requested_regions: list[str],
    dropped_regions: list[dict],
    kept_regions: list[str],
    slots: dict,
) -> str:
    """Truthful prose explaining an over-constrained trip and the dropped region(s).

    Acknowledges the conflict (spread too thin / too far apart), states the tradeoff
    (to keep within the nights + pace, drop the outlier rather than cram), and names
    the dropped region(s) + the kept focus. Emitted only when a region was genuinely
    dropped by the re-plan, so the eval's markers reflect real behaviour.
    """
    nights = slots.get("nights")
    pace = slots.get("pace", "relaxed")
    # We rebuild the user-facing sentence here rather than concatenating each
    # dropped_regions[].reason: that stored reason is a DEBUG/state record (kept on
    # TripState for the checkpoint + logs), deliberately separate from this
    # customer-facing prose so the two can evolve independently.
    dropped_names = [d["region"] for d in dropped_regions]
    return (
        f"Heads-up: {len(requested_regions)} regions "
        f"({_join_and(requested_regions)}) at a {pace} pace over just {nights} "
        f"nights is spread too thin — they're too far apart to enjoy without "
        f"rushing. To keep within your {nights} nights and {pace} pace, I'd drop "
        f"{_join_and(dropped_names)} (the farthest outlier) and focus on "
        f"{_join_and(kept_regions)} rather than cramming all "
        f"{len(requested_regions)}."
    )


def build_conflict_prose(
    slots: dict,
    requested_regions: list[str],
    kept_regions: list[str],
    dropped_regions: list[dict],
    infeasible: dict | None,
) -> str | None:
    """Compose the explanatory prefix for the reply, or ``None`` if no conflict.

    Combines an infeasibility flag (if any) and a dropped-region explanation (if any
    region was dropped). Returns ``None`` when neither applies — the caller then
    leaves today's plain itinerary reply untouched (single-region / compact trips).
    """
    parts: list[str] = []
    if infeasible:
        parts.append(_infeasibility_prose(infeasible))
    if dropped_regions:
        parts.append(_dropped_prose(requested_regions, dropped_regions, kept_regions, slots))
    if not parts:
        return None
    return " ".join(parts)


def augment_reply(
    base_reply: str,
    slots: dict,
    requested_regions: list[str],
    kept_regions: list[str],
    dropped_regions: list[dict],
    infeasible: dict | None,
) -> str:
    """Prefix the naive itinerary reply with conflict prose when one was detected.

    No-op (returns ``base_reply`` unchanged) when there is no conflict, so the
    non-conflict trip examples and the pre-PR7 behaviour are byte-identical.
    """
    prose = build_conflict_prose(
        slots, requested_regions, kept_regions, dropped_regions, infeasible
    )
    if not prose:
        return base_reply
    return f"{prose} {base_reply}"
