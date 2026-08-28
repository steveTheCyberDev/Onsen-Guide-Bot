"""Unit tests for the deterministic trip constraint rules (V3 PR7).

Covers ``agent/trip/constraints.py`` in isolation (pure functions over centroids,
no LLM / Chroma / Google) plus the end-to-end graph re-plan behaviour driven by the
``check_constraints`` node + back-edge. Geo fixtures reuse the rough prefecture
centroids from ``test_routing_service`` so the numbers are consistent.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.trip import itinerary as itinerary_module
from agent.trip import slots as slots_module
from agent.trip.constraints import (
    augment_reply,
    build_conflict_prose,
    detect_conflicts,
)
from agent.trip.graph import build_trip_graph
from agent.trip.slots import SlotUpdate

GIFU = [35.8, 137.0]
NAGANO = [36.6, 138.2]
SHIZUOKA = [34.9, 138.4]
OKINAWA = [26.2, 127.7]
AICHI = [35.0, 137.0]


# --- detect_conflicts: over-constrained (drop the farthest outlier) ----------


def test_over_constrained_three_dispersed_regions_drops_shizuoka():
    slots = {"regions": ["Gifu", "Nagano", "Shizuoka"], "nights": 3, "pace": "relaxed"}
    centroids = {"Gifu": GIFU, "Nagano": NAGANO, "Shizuoka": SHIZUOKA}
    report = detect_conflicts(slots, centroids, replan_count=0)
    assert report.over_constrained is True
    assert report.should_replan is True
    assert report.drop_region == "Shizuoka"  # the most-dispersed region
    assert report.drop_reason and "spread too thin" in report.drop_reason
    assert report.infeasible is None  # mainland cluster is reachable


def test_over_constrained_does_not_fire_for_two_regions():
    slots = {"regions": ["Gifu", "Shizuoka"], "nights": 3, "pace": "relaxed"}
    centroids = {"Gifu": GIFU, "Shizuoka": SHIZUOKA}
    report = detect_conflicts(slots, centroids, replan_count=0)
    assert report.over_constrained is False
    assert report.should_replan is False
    assert report.infeasible is None


def test_over_constrained_does_not_fire_when_nights_are_ample():
    # 3 regions but 6 nights (> regions) → the nights CAN absorb them.
    slots = {"regions": ["Gifu", "Nagano", "Shizuoka"], "nights": 6, "pace": "relaxed"}
    centroids = {"Gifu": GIFU, "Nagano": NAGANO, "Shizuoka": SHIZUOKA}
    report = detect_conflicts(slots, centroids, replan_count=0)
    assert report.over_constrained is False
    assert report.should_replan is False


def test_over_constrained_stops_replanning_after_the_bound():
    # replan_count already at the bound → verdict still flags over_constrained but
    # does NOT request another re-plan (loop is bounded).
    slots = {"regions": ["Gifu", "Nagano", "Shizuoka"], "nights": 3, "pace": "relaxed"}
    centroids = {"Gifu": GIFU, "Nagano": NAGANO, "Shizuoka": SHIZUOKA}
    report = detect_conflicts(slots, centroids, replan_count=1)
    assert report.over_constrained is True
    assert report.should_replan is False


def test_compact_three_regions_are_not_dispersed():
    # Three regions all at essentially the same point → not dispersed → no drop.
    slots = {"regions": ["A", "B", "C"], "nights": 3, "pace": "relaxed"}
    centroids = {"A": [35.0, 137.0], "B": [35.0, 137.01], "C": [35.0, 137.02]}
    report = detect_conflicts(slots, centroids, replan_count=0)
    assert report.over_constrained is False


# --- detect_conflicts: geographic infeasibility (advisory flag, no re-plan) ---


def test_infeasible_okinawa_gifu_is_flagged_not_replanned():
    slots = {"regions": ["Okinawa", "Gifu"], "nights": 4, "pace": "packed"}
    centroids = {"Okinawa": OKINAWA, "Gifu": GIFU}
    report = detect_conflicts(slots, centroids, replan_count=0)
    assert report.infeasible is not None
    assert set(report.infeasible["regions"]) == {"Okinawa", "Gifu"}
    assert report.infeasible["leg_km"] > 1000
    # Infeasibility never triggers a drop/re-plan — it can't be fixed by dropping.
    assert report.should_replan is False


def test_gifu_aichi_neither_infeasible_nor_over_constrained():
    slots = {"regions": ["Gifu", "Aichi"], "nights": 3, "pace": "relaxed"}
    centroids = {"Gifu": GIFU, "Aichi": AICHI}
    report = detect_conflicts(slots, centroids, replan_count=0)
    assert report.infeasible is None
    assert report.over_constrained is False
    assert report.should_replan is False


# --- prose: only produced when a conflict is genuinely detected ---------------


def test_no_prose_when_no_conflict():
    prose = build_conflict_prose(
        {"nights": 3, "pace": "relaxed"},
        requested_regions=["Gifu"],
        kept_regions=["Gifu"],
        dropped_regions=[],
        infeasible=None,
    )
    assert prose is None


def test_augment_reply_is_noop_without_conflict():
    base = "Here's a naive 3-night onsen itinerary — Gifu (3 nights): Gero Onsen."
    out = augment_reply(
        base, {"nights": 3, "pace": "relaxed"}, ["Gifu"], ["Gifu"], [], None
    )
    assert out == base


def test_dropped_prose_names_conflict_tradeoff_and_region():
    prose = build_conflict_prose(
        {"nights": 3, "pace": "relaxed"},
        requested_regions=["Gifu", "Nagano", "Shizuoka"],
        kept_regions=["Gifu", "Nagano"],
        dropped_regions=[{"region": "Shizuoka", "reason": "farthest outlier"}],
        infeasible=None,
    )
    assert prose is not None
    low = prose.lower()
    # conflict acknowledgement + tradeoff + drop, all naming the dropped region.
    assert "spread too thin" in low or "too far apart" in low
    assert "rather than" in low or "to keep within" in low
    assert "drop" in low and "shizuoka" in low


def test_infeasibility_prose_flags_flight_and_reshape():
    prose = build_conflict_prose(
        {"nights": 4, "pace": "packed"},
        requested_regions=["Okinawa", "Gifu"],
        kept_regions=["Okinawa", "Gifu"],
        dropped_regions=[],
        infeasible={"regions": ["Okinawa", "Gifu"], "leg_km": 1386.0},
    )
    assert prose is not None
    low = prose.lower()
    assert "isn't feasible" in low
    # Distance-grounded, modality-hedged: names a flight/shinkansen option but does
    # NOT fabricate a water crossing from distance alone.
    assert "too far apart" in low
    assert "flight" in low and "shinkansen" in low
    assert "over water" not in low
    assert "separate trips" in low or "reallocate" in low


# --- end-to-end graph re-plan ------------------------------------------------


def _cfg(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _onsen(name: str, lat: float, lng: float, prefecture: str) -> dict:
    return {
        "name": name,
        "location": f"City, {prefecture}",
        "spring_type": "Sulfur Spring",
        "spa_quality": f"{name} is a sulfur spring.",
        "detail_url": f"https://example.com/{name.replace(' ', '-')}",
        "lat": lat,
        "lng": lng,
    }


def _region_records() -> dict[str, list[dict]]:
    """Region → a couple of coord-bearing onsen at that region's centroid."""
    return {
        "Gifu": [_onsen("Gero Onsen", 35.8, 137.0, "Gifu"), _onsen("Hirayu Onsen", 35.85, 137.1, "Gifu")],
        "Nagano": [_onsen("Nozawa Onsen", 36.6, 138.2, "Nagano")],
        "Shizuoka": [_onsen("Atami Onsen", 34.9, 138.4, "Shizuoka")],
    }


def _retrieval_by_region(mapping: dict[str, list[dict]]) -> MagicMock:
    def _fn(query, prefecture=None, n_results=20):
        return list(mapping.get(prefecture, []))

    return MagicMock(side_effect=_fn)


def _mock_llm(update: SlotUpdate) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=update)
    return llm


@pytest.mark.asyncio
async def test_graph_replans_and_drops_outlier_for_over_constrained_trip():
    graph = build_trip_graph()
    session_id = "trip-pr7-over"
    full = SlotUpdate(
        regions=["Gifu", "Nagano", "Shizuoka"], nights=3, dates_or_season="autumn", pace="relaxed"
    )
    retrieval = _retrieval_by_region(_region_records())

    with patch.object(slots_module, "_llm", _mock_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(itinerary_module, "search_hotels", return_value=[]):
        r = await graph.ainvoke(
            {"message": "relaxed 3-night trip across Gifu, Nagano and Shizuoka"},
            config=_cfg(session_id),
        )

    snap = graph.get_state(_cfg(session_id)).values
    # The outlier (Shizuoka) was dropped and recorded with a reason.
    dropped = {d["region"] for d in snap.get("dropped_regions", [])}
    assert dropped == {"Shizuoka"}
    assert snap.get("replan_count") == 1
    # Final itinerary contains only the kept regions; nights still add up to 3.
    planned = {leg["region"] for leg in snap["itinerary"]["regions"]}
    assert planned == {"Gifu", "Nagano"}
    assert sum(leg["nights"] for leg in snap["itinerary"]["regions"]) == 3
    # Retrieval still covered ALL THREE requested regions (pass-1 tool selection).
    assert {c.kwargs["prefecture"] for c in retrieval.call_args_list} == {
        "Gifu", "Nagano", "Shizuoka",
    }
    # The reply explains the drop.
    low = r["reply"].lower()
    assert "drop" in low and "shizuoka" in low


@pytest.mark.asyncio
async def test_graph_flags_infeasible_trip_without_replanning():
    graph = build_trip_graph()
    session_id = "trip-pr7-infeasible"
    full = SlotUpdate(
        regions=["Okinawa", "Gifu"], nights=4, dates_or_season="spring", pace="packed"
    )
    records = {
        "Okinawa": [_onsen("Yamada Onsen", 26.2, 127.7, "Okinawa")],
        "Gifu": [_onsen("Gero Onsen", 35.8, 137.0, "Gifu")],
    }
    retrieval = _retrieval_by_region(records)

    with patch.object(slots_module, "_llm", _mock_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(itinerary_module, "search_hotels", return_value=[]):
        r = await graph.ainvoke(
            {"message": "packed 4-night trip: 2 nights Okinawa, 2 nights Gifu"},
            config=_cfg(session_id),
        )

    snap = graph.get_state(_cfg(session_id)).values
    # No region dropped (infeasibility isn't fixed by dropping); flag recorded.
    assert not snap.get("dropped_regions")
    assert snap.get("replan_count", 0) == 0
    assert snap.get("infeasible") is not None
    # Both regions still planned; nights add up.
    planned = {leg["region"] for leg in snap["itinerary"]["regions"]}
    assert planned == {"Okinawa", "Gifu"}
    assert sum(leg["nights"] for leg in snap["itinerary"]["regions"]) == 4
    low = r["reply"].lower()
    assert "isn't feasible" in low and "flight" in low
    assert "over water" not in low  # no fabricated modality from distance alone


@pytest.mark.asyncio
async def test_graph_two_region_trip_is_unchanged_no_conflict_prose():
    graph = build_trip_graph()
    session_id = "trip-pr7-clean"
    full = SlotUpdate(
        regions=["Gifu", "Nagano"], nights=4, dates_or_season="autumn", pace="relaxed"
    )
    retrieval = _retrieval_by_region(_region_records())

    with patch.object(slots_module, "_llm", _mock_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(itinerary_module, "search_hotels", return_value=[]):
        r = await graph.ainvoke(
            {"message": "relaxed 4-night trip across Gifu and Nagano"},
            config=_cfg(session_id),
        )

    snap = graph.get_state(_cfg(session_id)).values
    assert not snap.get("dropped_regions")
    assert snap.get("infeasible") is None
    # Plain itinerary reply — no conflict prefix.
    assert r["reply"].startswith("Here's a naive")
