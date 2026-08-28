"""PR5 tests — hotels per lodging stop in the trip planner.

The plan node now looks up nearby hotels for each selected onsen stop via
``search_hotels`` (Rakuten), following the workflow's fail-soft + off-event-loop
discipline: a Rakuten outage/error never breaks the plan, and a stop with no
hotels says so explicitly ("no hotels found nearby") — never fabricated.

All external calls are ALWAYS mocked so the suite is deterministic and free:
  * the gather extraction LLM — ``agent.trip.slots._llm`` (PR3b seam);
  * per-region retrieval — ``agent.trip.itinerary.query_onsen_structured``;
  * hotel lookup — ``agent.trip.itinerary.search_hotels`` (the conftest autouse
    fixture stubs it to [] by default; these tests override it explicitly).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.schemas import HotelResult
from agent.trip import itinerary as itinerary_module
from agent.trip import slots as slots_module
from agent.trip.agent import plan_trip
from agent.trip.graph import build_trip_graph
from agent.trip.itinerary import (
    _to_hotel,
    attach_hotels,
    build_itinerary,
    build_reply,
    hotel_results_from_itinerary,
)
from agent.trip.slots import SlotUpdate


def _cfg(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _onsen(name: str, lat: float = 36.0, lng: float = 137.0) -> dict:
    return {
        "name": name,
        "location": "Some City, Gifu",
        "spring_type": "Sulfur Spring",
        "spa_quality": f"{name} description.",
        "detail_url": f"https://example.com/{name}",
        "lat": lat,
        "lng": lng,
    }


def _hotel(name: str) -> dict:
    """A Rakuten-service-shaped hotel dict (what search_hotels returns)."""
    return {
        "name": name,
        "address": "Gifu, Japan",
        "price": 12000,
        "hotelSpecial": "Onsen ryokan",
        "hotelImageUrl": f"https://img.example.com/{name}.jpg",
        "url": f"https://travel.example.com/{name}",
        "lat": 36.0,
        "lng": 137.0,
    }


def _mock_llm(*updates: SlotUpdate) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=list(updates))
    return llm


def _mock_retrieval(*records: dict) -> MagicMock:
    return MagicMock(return_value=list(records))


# --- pure helpers ------------------------------------------------------------


def test_to_hotel_maps_rakuten_fields():
    r = _to_hotel(_hotel("Ryokan A"))
    assert isinstance(r, HotelResult)
    assert r.name == "Ryokan A" and r.originalName == "Ryokan A"
    assert r.price == "12000"  # coerced to str, matching the workflow mapping
    assert r.image == "https://img.example.com/Ryokan A.jpg"
    assert r.url == "https://travel.example.com/Ryokan A"


def test_attach_hotels_populates_each_stop_failsoft():
    itin = build_itinerary(
        {"regions": ["Gifu"], "nights": 2, "pace": "relaxed"},
        {"Gifu": [_onsen("Gero Onsen"), _onsen("Hirayu Onsen")]},
    )
    with patch.object(itinerary_module, "search_hotels", return_value=[_hotel("Ryokan A")]):
        attach_hotels(itin)
    for onsen in itin["regions"][0]["onsens"]:
        assert onsen["hotels"] == [_hotel("Ryokan A")]


def test_attach_hotels_is_failsoft_when_search_raises():
    itin = build_itinerary(
        {"regions": ["Gifu"], "nights": 1, "pace": "relaxed"},
        {"Gifu": [_onsen("Gero Onsen")]},
    )
    with patch.object(itinerary_module, "search_hotels", side_effect=RuntimeError("rakuten down")):
        attach_hotels(itin)  # must NOT raise
    assert itin["regions"][0]["onsens"][0]["hotels"] == []


def test_attach_hotels_skips_stops_without_coords():
    itin = build_itinerary(
        {"regions": ["Gifu"], "nights": 1, "pace": "relaxed"},
        {"Gifu": [_onsen("No Coords", lat=None, lng=None)]},
    )
    search = MagicMock(return_value=[_hotel("Ryokan A")])
    with patch.object(itinerary_module, "search_hotels", search):
        attach_hotels(itin)
    # No coords → no lookup, empty hotels (treated as "none found").
    assert itin["regions"][0]["onsens"][0]["hotels"] == []
    search.assert_not_called()


def test_hotel_results_dedupes_across_stops():
    # Two stops return an overlapping hotel; the flattened AgentResponse hotels
    # must de-dupe by (name, url).
    itin = build_itinerary(
        {"regions": ["Gifu"], "nights": 2, "pace": "relaxed"},
        {"Gifu": [_onsen("Gero Onsen"), _onsen("Hirayu Onsen")]},
    )
    itin["regions"][0]["onsens"][0]["hotels"] = [_hotel("Shared"), _hotel("A")]
    itin["regions"][0]["onsens"][1]["hotels"] = [_hotel("Shared"), _hotel("B")]
    results = hotel_results_from_itinerary(itin)
    names = [h.name for h in results]
    assert names == ["Shared", "A", "B"]  # Shared appears once, order preserved


def test_build_reply_names_hotels_and_flags_none_found():
    itin = build_itinerary(
        {"regions": ["Gifu"], "nights": 2, "pace": "relaxed"},
        {"Gifu": [_onsen("Gero Onsen"), _onsen("Hirayu Onsen")]},
    )
    itin["regions"][0]["onsens"][0]["hotels"] = [_hotel("Ryokan A"), _hotel("Ryokan B")]
    itin["regions"][0]["onsens"][1]["hotels"] = []  # looked, found none
    reply = build_reply(itin)
    assert "Gero Onsen (nearby hotels: Ryokan A, Ryokan B)" in reply
    assert "Hirayu Onsen (no hotels found nearby)" in reply


def test_build_reply_onsen_only_when_no_hotels_key():
    # An onsen-only itinerary (no hotel step run) must not mention lodging at all —
    # backward-compatible with the PR3c reply.
    itin = build_itinerary(
        {"regions": ["Gifu"], "nights": 1, "pace": "relaxed"},
        {"Gifu": [_onsen("Gero Onsen")]},
    )
    reply = build_reply(itin)
    assert "Gero Onsen" in reply
    assert "hotels" not in reply.lower()


# --- end-to-end via the graph / plan_trip ------------------------------------


@pytest.mark.asyncio
async def test_plan_trip_populates_hotels_per_stop():
    full = SlotUpdate(regions=["Gifu"], nights=2, dates_or_season="autumn")
    retrieval = _mock_retrieval(_onsen("Gero Onsen"), _onsen("Hirayu Onsen"))
    with patch.object(slots_module, "_llm", _mock_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(itinerary_module, "search_hotels", return_value=[_hotel("Ryokan A")]):
        resp = await plan_trip("2 nights in Gifu this autumn", session_id="pr5-hotels")

    assert resp.onsens and {o.name for o in resp.onsens} <= {"Gero Onsen", "Hirayu Onsen"}
    # Hotels populated (deduped to the single Ryokan A both stops returned).
    assert resp.hotels and all(isinstance(h, HotelResult) for h in resp.hotels)
    assert {h.name for h in resp.hotels} == {"Ryokan A"}
    assert "nearby hotels: Ryokan A" in resp.reply


@pytest.mark.asyncio
async def test_plan_trip_failsoft_when_rakuten_raises():
    full = SlotUpdate(regions=["Gifu"], nights=1, dates_or_season="autumn")
    retrieval = _mock_retrieval(_onsen("Gero Onsen"))
    with patch.object(slots_module, "_llm", _mock_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(itinerary_module, "search_hotels", side_effect=RuntimeError("rakuten 503")):
        resp = await plan_trip("1 night in Gifu this autumn", session_id="pr5-failsoft")

    # Still a valid plan response — onsen present, hotels empty, no exception, and
    # the stop is honestly flagged.
    assert resp.onsens and {o.name for o in resp.onsens} == {"Gero Onsen"}
    assert resp.hotels == []
    assert "no hotels found nearby" in resp.reply.lower()


@pytest.mark.asyncio
async def test_plan_trip_no_hotels_says_none_found_no_fabrication():
    full = SlotUpdate(regions=["Gifu"], nights=1, dates_or_season="autumn")
    retrieval = _mock_retrieval(_onsen("Gero Onsen"))
    with patch.object(slots_module, "_llm", _mock_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(itinerary_module, "search_hotels", return_value=[]):
        resp = await plan_trip("1 night in Gifu this autumn", session_id="pr5-nohotels")

    assert resp.hotels == []  # nothing fabricated
    assert "no hotels found nearby" in resp.reply.lower()


@pytest.mark.asyncio
async def test_elicit_turn_does_not_look_up_hotels():
    # A missing required slot short-circuits at elicit — the plan node (and its
    # hotel lookup) is never reached.
    search = MagicMock(return_value=[_hotel("Ryokan A")])
    with patch.object(slots_module, "_llm", _mock_llm(SlotUpdate(regions=["Gifu"]))), patch.object(
        itinerary_module, "search_hotels", search
    ):
        resp = await plan_trip("onsen trip in Gifu", session_id="pr5-elicit")

    assert resp.hotels == []
    assert resp.reply.count("?") == 1
    search.assert_not_called()


@pytest.mark.asyncio
async def test_hotels_stored_in_itinerary_state():
    graph = build_trip_graph()
    session_id = "pr5-state"
    full = SlotUpdate(regions=["Gifu"], nights=1, dates_or_season="autumn")
    with patch.object(slots_module, "_llm", _mock_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", _mock_retrieval(_onsen("Gero Onsen"))
    ), patch.object(itinerary_module, "search_hotels", return_value=[_hotel("Ryokan A")]):
        await graph.ainvoke({"message": "1 night in Gifu this autumn"}, config=_cfg(session_id))

    snap = graph.get_state(_cfg(session_id)).values
    stop = snap["itinerary"]["regions"][0]["onsens"][0]
    assert stop["hotels"] == [_hotel("Ryokan A")]  # JSON-serialisable raw dicts
