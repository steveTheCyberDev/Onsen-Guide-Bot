"""M1 regression — a follow-up may NARROW/REPLACE the trip's regions, not only ADD.

Encodes the production failure from the LangSmith trace of 2026-09-09 (thread
``779ace6d-37f4-4039-be4e-4cf36abaab0c``): after settling on Nagano + Ishikawa the
traveller asked "what about Hokkaido?", then twice tried to narrow the trip to
"Hokkaido only" — and got the SAME unresolved conflict reply both times ("Heads-up:
combining Nagano with Hokkaido isn't feasible"), never a Hokkaido-only itinerary.

Two defects produced that, and both are covered here:

  1. ``slots.merge_slots`` had no way to express REPLACE/REMOVE — the extraction LLM
     was asked to return the full merged region list, so "Hokkaido only" and
     "Hokkaido too" were indistinguishable deltas. Now the LLM classifies the intent
     (``SlotUpdate.regions_op``) and ``apply_region_op`` does the set arithmetic in
     Python.
  2. The PR7 re-plan scratch state (``dropped_regions`` / ``replan_count`` /
     ``infeasible``) was checkpointed and carried into the NEXT turn, so the narrowed
     region was still filtered out by the previous turn's drop and the reply replayed
     the previous turn's stale infeasibility prose. ``gather`` now resets it per turn.

The extraction LLM is always mocked at ``agent.trip.slots._llm`` (same seam as
``test_trip_slots_elicit_loop.py``); retrieval/hotels are mocked per region, so the
haversine verdicts are real but no Chroma/Rakuten/OpenAI call is made. A FRESH graph
is built per test so its MemorySaver starts empty.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.trip import itinerary as itinerary_module
from agent.trip import slots as slots_module
from agent.trip.graph import build_trip_graph
from agent.trip.slots import SlotUpdate, TripSlots, apply_region_op, merge_slots


def _cfg(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _onsen(name: str, lat: float, lng: float, region: str) -> dict:
    """A coord-bearing query_onsen_structured-shaped record for the plan node."""
    return {
        "name": name,
        "location": f"{region}, Japan",
        "spring_type": "Sulfur Spring",
        "spa_quality": "A relaxing sulfur spring.",
        "detail_url": f"https://example.com/{name}",
        "lat": lat,
        "lng": lng,
    }


# Real-ish centroids so the haversine rules fire exactly as they did in prod:
# Nagano↔Hokkaido is ~800 km (over trip_infeasible_leg_km=500), Nagano↔Gifu is not.
_REGION_RECORDS: dict[str, list[dict]] = {
    "Nagano": [_onsen("Nozawa Onsen", 36.6, 138.2, "Nagano")],
    "Gifu": [_onsen("Gero Onsen", 35.8, 137.0, "Gifu")],
    "Shizuoka": [_onsen("Atami Onsen", 34.9, 138.4, "Shizuoka")],
    "Hokkaido": [_onsen("Noboribetsu Onsen", 42.5, 141.1, "Hokkaido")],
}


def _retrieval_by_region() -> MagicMock:
    def _fn(query, prefecture=None, n_results=20):
        return [dict(r) for r in _REGION_RECORDS.get(prefecture, [])]

    return MagicMock(side_effect=_fn)


def _mock_llm(*updates: SlotUpdate) -> MagicMock:
    """One scripted ``SlotUpdate`` per turn — ``extract_slots`` awaits ainvoke once."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=list(updates))
    return llm


def _planned_regions(snapshot: dict) -> set[str]:
    return {leg["region"] for leg in (snapshot["itinerary"] or {}).get("regions", [])}


# --- pure merge semantics (no graph, no LLM) ---------------------------------


def test_apply_region_op_replace_add_remove():
    current = ["Nagano", "Ishikawa"]
    # replace → the message states the complete new set.
    assert apply_region_op(current, ["Hokkaido"], "replace") == ["Hokkaido"]
    # add → union, prior order preserved, no duplicates.
    assert apply_region_op(current, ["Hokkaido"], "add") == [
        "Nagano", "Ishikawa", "Hokkaido",
    ]
    assert apply_region_op(current, ["Nagano"], "add") == current
    # remove → drop the named ones (case-insensitive), keep the rest.
    assert apply_region_op(current, ["nagano"], "remove") == ["Ishikawa"]
    # Removing everything is allowed — the elicit gate then re-asks for an area.
    assert apply_region_op(current, ["Nagano", "Ishikawa"], "remove") == []
    # No op → replace (historical default: a bare list is the complete set).
    assert apply_region_op(current, ["Hokkaido"], None) == ["Hokkaido"]


def test_merge_slots_narrows_on_replace_and_still_adds_on_add():
    current = TripSlots(regions=["Nagano", "Ishikawa"], nights=3, dates_or_season="autumn")
    # The ADD case must keep working ("what about Hokkaido too?").
    added = merge_slots(current, SlotUpdate(regions=["Hokkaido"], regions_op="add"))
    assert added.regions == ["Nagano", "Ishikawa", "Hokkaido"]
    # The REPLACE case is the fix ("Hokkaido only").
    narrowed = merge_slots(added, SlotUpdate(regions=["Hokkaido"], regions_op="replace"))
    assert narrowed.regions == ["Hokkaido"]
    # The REMOVE case ("drop Nagano") narrows without restating the keepers.
    pruned = merge_slots(added, SlotUpdate(regions=["Nagano"], regions_op="remove"))
    assert pruned.regions == ["Ishikawa", "Hokkaido"]
    # Every other slot survives all three.
    for merged in (added, narrowed, pruned):
        assert merged.nights == 3 and merged.dates_or_season == "autumn"


def test_regions_op_is_not_written_onto_trip_slots():
    # regions_op routes the merge; it must never leak into the canonical slot state
    # (it would then be echoed back to the LLM as a "slot gathered so far").
    merged = merge_slots(TripSlots(), SlotUpdate(regions=["Gifu"], regions_op="add"))
    assert not hasattr(merged, "regions_op")
    assert "regions_op" not in merged.model_dump()


def test_empty_or_absent_regions_delta_never_touches_prior_regions():
    current = TripSlots(regions=["Gifu", "Nagano"])
    # Nothing mentioned at all.
    assert merge_slots(current, SlotUpdate(nights=4)).regions == ["Gifu", "Nagano"]
    # An empty list with an op is still "mentioned nothing" — must not wipe.
    assert merge_slots(current, SlotUpdate(regions=[], regions_op="replace")).regions == [
        "Gifu", "Nagano",
    ]


# --- the trace: add → conflict → narrow, all on one session ------------------


@pytest.mark.asyncio
async def test_narrowing_follow_up_replans_to_a_clean_single_region_itinerary():
    """The exact production sequence — the third turn must resolve, not repeat."""
    graph = build_trip_graph()
    session_id = "trip-narrow-hokkaido"

    turns = [
        # 1. Settled multi-region trip (no conflict: Nagano + Gifu are close).
        SlotUpdate(
            regions=["Nagano", "Gifu"], nights=3, dates_or_season="autumn", pace="relaxed"
        ),
        # 2. "what about Hokkaido?" → ADD → 3 dispersed regions over 3 nights
        #    → infeasible flag + over-constrained drop of the Hokkaido outlier.
        SlotUpdate(regions=["Hokkaido"], regions_op="add"),
        # 3. "I mean Hokkaido only, drop Nagano and Gifu" → REPLACE.
        SlotUpdate(regions=["Hokkaido"], regions_op="replace"),
    ]
    retrieval = _retrieval_by_region()

    with patch.object(slots_module, "_llm", _mock_llm(*turns)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(itinerary_module, "search_hotels", return_value=[]):
        r1 = await graph.ainvoke(
            {"message": "3 nights in Nagano and Gifu this autumn"}, config=_cfg(session_id)
        )
        r2 = await graph.ainvoke({"message": "what about Hokkaido?"}, config=_cfg(session_id))
        snap2 = graph.get_state(_cfg(session_id)).values
        r3 = await graph.ainvoke(
            {"message": "I mean search Hokkaido only, drop Nagano and Gifu"},
            config=_cfg(session_id),
        )

    # Turn 1 — a plain two-region itinerary, no conflict prose.
    assert r1["reply"].startswith("Here's a naive")

    # Turn 2 — the ADD case still works, and the conflict fires as it did in prod:
    # Hokkaido was added, flagged infeasible, and dropped as the farthest outlier.
    assert snap2["slots"]["regions"] == ["Nagano", "Gifu", "Hokkaido"]
    assert {d["region"] for d in snap2["dropped_regions"]} == {"Hokkaido"}
    assert snap2["infeasible"] is not None
    assert "isn't feasible" in r2["reply"].lower()

    # Turn 3 — THE REGRESSION. The region list actually narrows...
    snap3 = graph.get_state(_cfg(session_id)).values
    assert snap3["slots"]["regions"] == ["Hokkaido"]
    # ...the stale re-plan scratch state is cleared, so Hokkaido is no longer
    # filtered out of the plan and the re-plan budget is fresh...
    assert snap3["dropped_regions"] == []
    assert snap3["replan_count"] == 0
    assert snap3["infeasible"] is None
    # ...a real Hokkaido-only itinerary is built (one region can't conflict)...
    assert _planned_regions(snap3) == {"Hokkaido"}
    assert [o["name"] for o in snap3["itinerary"]["selected_onsens"]] == [
        "Noboribetsu Onsen"
    ]
    # ...and the reply is a clean itinerary, NOT the previous turn's conflict text.
    assert r3["reply"].startswith("Here's a naive")
    assert r3["reply"] != r2["reply"]
    low3 = r3["reply"].lower()
    assert "isn't feasible" not in low3
    assert "heads-up" not in low3
    assert "nagano" not in low3 and "gifu" not in low3
    assert "noboribetsu onsen" in low3
    # The other gathered slots survived the narrowing.
    assert snap3["slots"]["nights"] == 3
    assert snap3["slots"]["dates_or_season"] == "autumn"


@pytest.mark.asyncio
async def test_remove_follow_up_drops_only_the_named_region():
    """"drop Shizuoka" narrows to the keepers without restating them."""
    graph = build_trip_graph()
    session_id = "trip-narrow-remove"
    turns = [
        SlotUpdate(
            regions=["Gifu", "Nagano", "Shizuoka"], nights=3,
            dates_or_season="autumn", pace="relaxed",
        ),
        SlotUpdate(regions=["Shizuoka"], regions_op="remove"),
    ]
    retrieval = _retrieval_by_region()

    with patch.object(slots_module, "_llm", _mock_llm(*turns)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(itinerary_module, "search_hotels", return_value=[]):
        await graph.ainvoke(
            {"message": "3 nights across Gifu, Nagano and Shizuoka"}, config=_cfg(session_id)
        )
        r2 = await graph.ainvoke({"message": "drop Shizuoka"}, config=_cfg(session_id))

    snap = graph.get_state(_cfg(session_id)).values
    assert snap["slots"]["regions"] == ["Gifu", "Nagano"]
    # Two close regions over 3 nights is no longer over-constrained → clean plan.
    assert snap["dropped_regions"] == []
    assert _planned_regions(snap) == {"Gifu", "Nagano"}
    assert r2["reply"].startswith("Here's a naive")


@pytest.mark.asyncio
async def test_narrowing_to_nothing_re_elicits_for_an_area():
    """Removing every region is honest state, not a crash or a fabricated plan."""
    graph = build_trip_graph()
    session_id = "trip-narrow-empty"
    turns = [
        SlotUpdate(regions=["Gifu"], nights=2, dates_or_season="autumn"),
        SlotUpdate(regions=["Gifu"], regions_op="remove"),
    ]
    retrieval = _retrieval_by_region()

    with patch.object(slots_module, "_llm", _mock_llm(*turns)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(itinerary_module, "search_hotels", return_value=[]):
        await graph.ainvoke({"message": "2 nights in Gifu in autumn"}, config=_cfg(session_id))
        r2 = await graph.ainvoke({"message": "actually not Gifu"}, config=_cfg(session_id))

    snap = graph.get_state(_cfg(session_id)).values
    assert snap["slots"]["regions"] == []
    # Back to the elicit branch, asking the highest-priority missing slot (regions).
    assert "area" in r2["reply"].lower() and r2["reply"].count("?") == 1


@pytest.mark.asyncio
async def test_unchanged_regions_re_derive_the_same_conflict_verdict():
    """Resetting the scratch state must not make a still-conflicting trip go quiet."""
    graph = build_trip_graph()
    session_id = "trip-narrow-stable"
    turns = [
        SlotUpdate(
            regions=["Gifu", "Nagano", "Hokkaido"], nights=3,
            dates_or_season="autumn", pace="relaxed",
        ),
        # A follow-up that changes nothing about the regions.
        SlotUpdate(spring_or_scenery_prefs="sulfur springs"),
    ]
    retrieval = _retrieval_by_region()

    with patch.object(slots_module, "_llm", _mock_llm(*turns)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(itinerary_module, "search_hotels", return_value=[]):
        r1 = await graph.ainvoke(
            {"message": "3 nights across Gifu, Nagano and Hokkaido"}, config=_cfg(session_id)
        )
        r2 = await graph.ainvoke({"message": "I like sulfur springs"}, config=_cfg(session_id))

    snap = graph.get_state(_cfg(session_id)).values
    # Turn 2 re-derived the identical verdict from the unchanged regions — the drop
    # is recomputed, not remembered.
    assert {d["region"] for d in snap["dropped_regions"]} == {"Hokkaido"}
    assert snap["replan_count"] == 1
    assert snap["infeasible"] is not None
    for reply in (r1["reply"], r2["reply"]):
        assert "isn't feasible" in reply.lower()
        assert "hokkaido" in reply.lower()
