"""Unit tests for agent.workflow.pipeline.run_workflow.

``run_workflow(message, session_id)`` is the deterministic V2 pipeline. It chains
an LLM intent-parse hop (``parse_intent``), a pure-Python structured retrieval
(``query_onsen_structured``), an optional Rakuten hotel passthrough
(``search_hotels``), and a template reply — then persists via ``save_message``.

Every external dependency is mocked AT THE PIPELINE MODULE'S NAMESPACE
(``patch.object(pipeline, "...")``) because pipeline.py imports each name into its
own module, so patching the source module would not affect the bound reference:

    - parse_intent           AsyncMock returning a canned Intent (the only LLM hop)
    - query_onsen_structured returns a list of record dicts (Chroma metadata shape)
    - search_hotels          returns raw Rakuten hotel dicts (sync; called via to_thread)
    - get_history            returns a history list, threaded into parse_intent
    - save_message           the persistence sink

Tests assert the response CONTRACT (reply is a non-empty string, onsens/hotels
field mapping, which collaborators were/weren't called) — not exact reply wording.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.workflow import pipeline
from agent.workflow.intent import Intent


def _record(
    name="Test Onsen",
    location="Beppu",
    spring_type="Sulfur",
    spa_quality="Sulfur spring",
    lat=33.2846,
    lng=131.4914,
    **extra,
):
    """A query_onsen_structured record dict (Chroma metadata shape).

    Carries the OnsenResult-accepted keys; `extra` lets a test add the
    description/detail_url keys that the pipeline must drop.
    """
    rec = {
        "name": name,
        "location": location,
        "spring_type": spring_type,
        "spa_quality": spa_quality,
        "lat": lat,
        "lng": lng,
    }
    rec.update(extra)
    return rec


def _patch_pipeline(intent, records, hotels=None, history=None):
    """Patch every external dependency on the pipeline module namespace.

    Returns the contextmanager-entered mock handles via a dict so tests can make
    call assertions. Use as ``with _patches(...) as m:``.
    """
    hotels = hotels if hotels is not None else []
    history = history if history is not None else []
    return {
        "parse_intent": patch.object(
            pipeline, "parse_intent", new=AsyncMock(return_value=intent)
        ),
        "query_onsen_structured": patch.object(
            pipeline, "query_onsen_structured", return_value=records
        ),
        "search_hotels": patch.object(
            pipeline, "search_hotels", return_value=hotels
        ),
        "get_history": patch.object(
            pipeline, "get_history", return_value=history
        ),
        "save_message": patch.object(pipeline, "save_message"),
    }


class _Patched:
    """Enter all pipeline dependency patches together, exposing each mock."""

    def __init__(self, intent, records, hotels=None, history=None):
        self._cms = _patch_pipeline(intent, records, hotels, history)
        self.mocks = {}

    def __enter__(self):
        for key, cm in self._cms.items():
            self.mocks[key] = cm.__enter__()
        return self.mocks

    def __exit__(self, *exc):
        for cm in self._cms.values():
            cm.__exit__(*exc)
        return False


@pytest.mark.asyncio
async def test_happy_path_no_hotels_maps_onsens_and_skips_hotel_search():
    # Arrange — intent wants no hotels; retrieval returns two onsen records.
    intent = Intent(prefecture="Oita", query="sulfur onsen", wants_hotels=False)
    records = [
        _record(name="Beppu Onsen", location="Beppu"),
        _record(name="Yufuin Onsen", location="Yufuin", lat=33.26, lng=131.36),
    ]
    with _Patched(intent, records) as m:
        # Act
        result = await pipeline.run_workflow("sulfur onsen in Oita", "s1")

    # Assert — contract: reply present, two mapped onsens, no hotels.
    assert isinstance(result["reply"], str) and result["reply"]
    assert len(result["onsens"]) == 2
    assert result["onsens"][0]["name"] == "Beppu Onsen"
    assert result["onsens"][0]["location"] == "Beppu"
    assert result["onsens"][1]["name"] == "Yufuin Onsen"
    assert result["hotels"] == []
    # No lodging requested → Rakuten must not be touched.
    m["search_hotels"].assert_not_called()


@pytest.mark.asyncio
async def test_onsen_mapping_drops_description_and_detail_url_extras():
    # Arrange — records carry extra keys OnsenResult forbids; pipeline must
    # project onto the allow-list rather than crash on the extras.
    intent = Intent(prefecture="Shizuoka", query="ocean view", wants_hotels=False)
    records = [
        _record(
            name="Atami Onsen",
            location="Atami",
            lat=35.1,
            lng=139.07,
            description="A long Japanese-translated blurb that must be dropped.",
            detail_url="https://example.com/atami",
        )
    ]
    with _Patched(intent, records):
        # Act — must not raise on the extra keys.
        result = await pipeline.run_workflow("ocean view onsen", "s2")

    # Assert — accepted fields carried through; forbidden extras absent.
    onsen = result["onsens"][0]
    assert onsen["name"] == "Atami Onsen"
    assert onsen["lat"] == 35.1
    assert onsen["lng"] == 139.07
    assert "description" not in onsen
    assert "detail_url" not in onsen


@pytest.mark.asyncio
async def test_hotels_path_calls_search_with_first_coord_onsen_and_maps_passthrough():
    # Arrange — wants_hotels; first onsen has both coords, so search_hotels runs.
    intent = Intent(prefecture="Oita", query="onsen ryokan", wants_hotels=True)
    records = [_record(name="Beppu Onsen", lat=33.2846, lng=131.4914)]
    raw_hotels = [
        {
            "name": "別府温泉ホテル",
            "address": "Beppu, Oita",
            "hotelSpecial": "Ocean view",
            "price": 12000,
            "hotelImageUrl": "https://img.example.com/h.jpg",
            "url": "https://example.com/hotel",
            "lat": 33.28,
            "lng": 131.49,
        }
    ]
    with _Patched(intent, records, hotels=raw_hotels) as m:
        # Act
        result = await pipeline.run_workflow("onsen ryokan in Beppu", "s3")

    # Assert — search_hotels called with the first-coord onsen's lat/lng.
    m["search_hotels"].assert_called_once()
    call_args = m["search_hotels"].call_args.args
    assert call_args == (33.2846, 131.4914)

    # Hotel passthrough mapping: name == originalName == the Japanese string.
    assert len(result["hotels"]) == 1
    hotel = result["hotels"][0]
    assert hotel["name"] == "別府温泉ホテル"
    assert hotel["originalName"] == "別府温泉ホテル"
    assert hotel["location"] == "Beppu, Oita"
    assert hotel["hotelSpecial"] == "Ocean view"
    assert hotel["price"] == "12000"
    assert hotel["image"] == "https://img.example.com/h.jpg"
    assert hotel["url"] == "https://example.com/hotel"
    assert hotel["lat"] == 33.28
    assert hotel["lng"] == 131.49


@pytest.mark.asyncio
async def test_hotels_path_uses_first_onsen_with_coords_when_earlier_ones_lack_them():
    # Arrange — wants_hotels; first onsen lacks coords, second has them. The
    # pipeline must pick the FIRST onsen that has BOTH lat and lng.
    intent = Intent(prefecture="Oita", query="onsen ryokan", wants_hotels=True)
    records = [
        _record(name="No Coords Onsen", lat=None, lng=None),
        _record(name="Has Coords Onsen", lat=33.5, lng=131.5),
    ]
    with _Patched(intent, records, hotels=[]) as m:
        # Act
        await pipeline.run_workflow("onsen ryokan", "s3b")

    # Assert — coords came from the second (first fully-coord) onsen.
    m["search_hotels"].assert_called_once_with(33.5, 131.5)


@pytest.mark.asyncio
async def test_wants_hotels_but_no_onsen_has_coords_skips_search_and_returns_empty_hotels():
    # Arrange — wants_hotels but every onsen has null coords → no geocoding,
    # no Rakuten call, empty hotels.
    intent = Intent(prefecture="Nagano", query="quiet onsen", wants_hotels=True)
    records = [
        _record(name="A", lat=None, lng=None),
        _record(name="B", lat=None, lng=None),
    ]
    with _Patched(intent, records) as m:
        # Act
        result = await pipeline.run_workflow("onsen to stay at", "s4")

    # Assert
    assert result["hotels"] == []
    assert len(result["onsens"]) == 2
    m["search_hotels"].assert_not_called()


@pytest.mark.asyncio
async def test_empty_retrieval_returns_no_onsen_reply_and_no_hotel_search():
    # Arrange — retrieval finds nothing.
    intent = Intent(prefecture="Aomori", query="impossible onsen", wants_hotels=True)
    with _Patched(intent, records=[]) as m:
        # Act
        result = await pipeline.run_workflow("an onsen that does not exist", "s5")

    # Assert — empty onsens, "no/none found" reply branch, no hotels/search.
    assert result["onsens"] == []
    assert result["hotels"] == []
    reply = result["reply"]
    assert isinstance(reply, str) and reply
    lowered = reply.lower()
    assert "no" in lowered or "none" in lowered
    # wants_hotels is True but there are no onsens → search must be skipped.
    m["search_hotels"].assert_not_called()


@pytest.mark.asyncio
async def test_save_message_called_once_with_session_message_and_reply():
    # Arrange
    intent = Intent(prefecture="Oita", query="onsen", wants_hotels=False)
    records = [_record(name="Beppu Onsen")]
    with _Patched(intent, records) as m:
        # Act
        result = await pipeline.run_workflow("any onsen?", "session-42")

    # Assert — persisted exactly once with (session_id, message, reply).
    m["save_message"].assert_called_once_with("session-42", "any onsen?", result["reply"])


@pytest.mark.asyncio
async def test_history_from_get_history_is_passed_to_parse_intent():
    # Arrange — get_history returns a sentinel list that must be threaded into
    # parse_intent's second positional argument.
    history = [MagicMock(name="history-entry")]
    intent = Intent(prefecture="Oita", query="onsen", wants_hotels=False)
    records = [_record(name="Beppu Onsen")]
    with _Patched(intent, records, history=history) as m:
        # Act
        await pipeline.run_workflow("any onsen?", "s6")

    # Assert — get_history called with the session id; its return value handed
    # to parse_intent as (message, history).
    m["get_history"].assert_called_once_with("s6")
    m["parse_intent"].assert_awaited_once()
    args = m["parse_intent"].await_args.args
    assert args[0] == "any onsen?"
    assert args[1] is history
