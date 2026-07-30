"""Unit tests for services.translation.translation_service and its two call sites.

The hotel-translation feature translates Rakuten hotel name/details JA→EN once and
caches by Rakuten hotel id. These tests exercise it deterministically and for FREE:
the OpenAI client is mocked at ``translation_service._get_client`` (no network) and
the persistent cache is repointed at a throwaway SQLite file per test (no shared
state, the real sessions.db is never touched), mirroring test_chat_service's
``session_db`` fixture.

Coverage: gate-off no-op (no LLM, no keys), cache MISS translates + stores, cache
HIT skips the LLM, fail-soft falls back to Japanese on LLM error, one batched call
for many hotels, no cache-poisoning on partial/malformed responses, cache-hit
survives a mid-flight failure, chunking of oversized batches, and all THREE wiring
surfaces (the /hotels route, the /chat workflow, and the trip itinerary) producing
English.
"""

import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from core.config import settings
from services.translation import translation_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def cache_db(tmp_path, monkeypatch):
    """Point the translation cache at a fresh throwaway SQLite file per test.

    Rebuilds the engine before AND after so no test leaks rows into another and the
    real local sessions.db is never touched. Mirrors test_chat_service.session_db.
    """
    db_file = tmp_path / "hotel_cache_test.db"
    monkeypatch.setattr(settings, "session_db_url", f"sqlite:///{db_file}")
    translation_service._reset_engine_for_tests()
    yield
    translation_service._reset_engine_for_tests()


@pytest.fixture
def gate_on(monkeypatch):
    """Enable the hotel-translation gate for a test (default is OFF)."""
    monkeypatch.setattr(settings, "hotel_translation_enabled", True)


def _fake_client(translations: list[dict]) -> MagicMock:
    """A mock OpenAI client whose chat completion returns ``translations`` as JSON."""
    client = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps(translations, ensure_ascii=False)
    client.chat.completions.create.return_value = resp
    return client


def _ja_hotel(hotel_id=123, name="ホテル日航那覇", special="天然温泉", address="沖縄県那覇市") -> dict:
    """A Rakuten-service-shaped hotel dict (Japanese source text)."""
    return {
        "id": hotel_id,
        "name": name,
        "address": address,
        "price": 8000,
        "hotelSpecial": special,
        "hotelImageUrl": "https://img.example.com/h.jpg",
        "url": "https://travel.example.com/h",
        "lat": 26.2124,
        "lng": 127.6809,
    }


def _en(idx=0, name_en="Hotel Nikko Naha", detail_en="Natural hot spring", location_en="Naha, Okinawa") -> dict:
    return {"idx": idx, "name_en": name_en, "detail_en": detail_en, "location_en": location_en}


# ---------------------------------------------------------------------------
# Gate OFF — clean no-op
# ---------------------------------------------------------------------------
def test_gate_off_is_noop_no_llm_call():
    """Gate off (default): no LLM call at all — pure Japanese passthrough."""
    hotels = [_ja_hotel()]
    with patch.object(translation_service, "_get_client") as get_client:
        result = translation_service.translate_hotels(hotels)
    get_client.assert_not_called()
    assert result is hotels


def test_gate_off_adds_no_english_keys():
    """Gate off: no ``*_en`` keys are added, so callers fall back to Japanese."""
    hotels = [_ja_hotel()]
    translation_service.translate_hotels(hotels)
    assert "name_en" not in hotels[0]


# ---------------------------------------------------------------------------
# Cache MISS — translate + store
# ---------------------------------------------------------------------------
def test_cache_miss_translates_fields(cache_db, gate_on):
    hotels = [_ja_hotel()]
    with patch.object(translation_service, "_get_client", return_value=_fake_client([_en()])):
        translation_service.translate_hotels(hotels)
    assert hotels[0]["name_en"] == "Hotel Nikko Naha"
    assert hotels[0]["hotelSpecial_en"] == "Natural hot spring"
    assert hotels[0]["location_en"] == "Naha, Okinawa"


def test_cache_miss_stores_translation(cache_db, gate_on):
    """A miss persists the translation so a later lookup is a hit."""
    hotels = [_ja_hotel(hotel_id=456)]
    with patch.object(translation_service, "_get_client", return_value=_fake_client([_en()])):
        translation_service.translate_hotels(hotels)
    cached = translation_service._get_cached(["456"])
    assert cached["456"]["name_en"] == "Hotel Nikko Naha"


def test_original_japanese_name_preserved_on_hotel(cache_db, gate_on):
    """Translation adds ``name_en`` but leaves the Japanese ``name`` untouched."""
    hotels = [_ja_hotel(name="草津温泉ホテル")]
    with patch.object(translation_service, "_get_client", return_value=_fake_client([_en()])):
        translation_service.translate_hotels(hotels)
    assert hotels[0]["name"] == "草津温泉ホテル"  # source intact
    assert hotels[0]["name_en"] == "Hotel Nikko Naha"


# ---------------------------------------------------------------------------
# Cache HIT — skip the LLM
# ---------------------------------------------------------------------------
def test_cache_hit_skips_llm(cache_db, gate_on):
    """A pre-cached id is served from the DB without any LLM call."""
    translation_service._store(
        [{
            "hotel_id": "789",
            "name_en": "Cached Hotel",
            "hotel_special_en": "Cached onsen",
            "location_en": "Cached City",
        }]
    )
    hotels = [_ja_hotel(hotel_id=789)]
    client = _fake_client([_en()])
    with patch.object(translation_service, "_get_client", return_value=client):
        translation_service.translate_hotels(hotels)
    client.chat.completions.create.assert_not_called()
    assert hotels[0]["name_en"] == "Cached Hotel"
    assert hotels[0]["location_en"] == "Cached City"


def test_partial_cache_only_translates_misses(cache_db, gate_on):
    """With one hotel cached and one new, only the miss is sent to the LLM."""
    translation_service._store(
        [{"hotel_id": "1", "name_en": "Cached One", "hotel_special_en": None, "location_en": None}]
    )
    hotels = [_ja_hotel(hotel_id=1), _ja_hotel(hotel_id=2, name="ホテルB")]
    client = _fake_client([_en(idx=0, name_en="Translated Two")])
    with patch.object(translation_service, "_get_client", return_value=client):
        translation_service.translate_hotels(hotels)
    # Exactly one LLM call, and its payload contained only the single miss.
    client.chat.completions.create.assert_called_once()
    sent = json.loads(client.chat.completions.create.call_args.kwargs["messages"][1]["content"])
    assert len(sent) == 1
    assert hotels[0]["name_en"] == "Cached One"
    assert hotels[1]["name_en"] == "Translated Two"


# ---------------------------------------------------------------------------
# Batch — one LLM call for many hotels
# ---------------------------------------------------------------------------
def test_batch_translates_multiple_in_one_call(cache_db, gate_on):
    hotels = [_ja_hotel(hotel_id=10, name="ホテルA"), _ja_hotel(hotel_id=20, name="ホテルB")]
    translations = [_en(idx=0, name_en="Hotel A"), _en(idx=1, name_en="Hotel B")]
    client = _fake_client(translations)
    with patch.object(translation_service, "_get_client", return_value=client):
        translation_service.translate_hotels(hotels)
    client.chat.completions.create.assert_called_once()
    assert hotels[0]["name_en"] == "Hotel A"
    assert hotels[1]["name_en"] == "Hotel B"


# ---------------------------------------------------------------------------
# Fail-soft — fall back to Japanese, never raise
# ---------------------------------------------------------------------------
def test_llm_error_falls_back_to_japanese(cache_db, gate_on):
    hotels = [_ja_hotel(name="ホテル日航那覇", special="天然温泉", address="沖縄県那覇市")]
    failing = _fake_client([])
    failing.chat.completions.create.side_effect = RuntimeError("OpenAI down")
    with patch.object(translation_service, "_get_client", return_value=failing):
        result = translation_service.translate_hotels(hotels)  # must NOT raise
    assert result is hotels
    assert hotels[0]["name_en"] == "ホテル日航那覇"  # JA fallback
    assert hotels[0]["hotelSpecial_en"] == "天然温泉"
    assert hotels[0]["location_en"] == "沖縄県那覇市"


def test_llm_error_does_not_store(cache_db, gate_on):
    """A failed translation must not poison the cache with Japanese text."""
    hotels = [_ja_hotel(hotel_id=999)]
    failing = _fake_client([])
    failing.chat.completions.create.side_effect = RuntimeError("boom")
    with patch.object(translation_service, "_get_client", return_value=failing):
        translation_service.translate_hotels(hotels)
    assert translation_service._get_cached(["999"]) == {}


def test_cache_read_failure_falls_back_to_japanese(cache_db, gate_on):
    """Even a cache-read error fails soft to Japanese (last-resort guard)."""
    hotels = [_ja_hotel(name="失敗ホテル")]
    with patch.object(translation_service, "_get_cached", side_effect=RuntimeError("db gone")):
        result = translation_service.translate_hotels(hotels)
    assert result is hotels
    assert hotels[0]["name_en"] == "失敗ホテル"


def test_empty_list_is_noop(cache_db, gate_on):
    with patch.object(translation_service, "_get_client") as get_client:
        assert translation_service.translate_hotels([]) == []
    get_client.assert_not_called()


def test_hotel_without_id_translated_but_not_cached(cache_db, gate_on):
    """A hotel with no id is still translated, just never persisted."""
    hotel = _ja_hotel()
    hotel["id"] = None
    with patch.object(translation_service, "_get_client", return_value=_fake_client([_en()])):
        translation_service.translate_hotels([hotel])
    assert hotel["name_en"] == "Hotel Nikko Naha"


# ---------------------------------------------------------------------------
# Wiring — the /hotels route produces English
# ---------------------------------------------------------------------------
def test_hotels_route_returns_english(client, cache_db, gate_on):
    raw = [_ja_hotel()]
    fake = _fake_client([_en(name_en="Hotel Nikko Naha", location_en="Naha, Okinawa")])
    with patch("api.routes.hotels.search_hotels", new=Mock(return_value=raw)), \
         patch.object(translation_service, "_get_client", return_value=fake):
        hotel = client.post(
            "/hotels", json={"latitude": 26.2124, "longitude": 127.6809, "radius": 3}
        ).json()["hotels"][0]
    assert hotel["name"] == "Hotel Nikko Naha"           # English visible name
    assert hotel["originalName"] == "ホテル日航那覇"        # Japanese kept
    assert hotel["location"] == "Naha, Okinawa"          # English location


# ---------------------------------------------------------------------------
# Wiring — the trip itinerary produces English
# ---------------------------------------------------------------------------
def test_trip_attach_hotels_produces_english(cache_db, gate_on):
    from agent.trip import itinerary as itinerary_module
    from agent.trip.itinerary import _to_hotel

    itinerary = {
        "regions": [
            {"region": "Okinawa", "nights": 1, "no_data": False,
             "onsens": [{"name": "Yamada Onsen", "lat": 26.2, "lng": 127.6}]}
        ]
    }
    fake = _fake_client([_en(name_en="Hotel Nikko Naha")])
    with patch.object(itinerary_module, "search_hotels", return_value=[_ja_hotel()]), \
         patch.object(translation_service, "_get_client", return_value=fake):
        itinerary_module.attach_hotels(itinerary)

    hotel_dict = itinerary["regions"][0]["onsens"][0]["hotels"][0]
    assert hotel_dict["name_en"] == "Hotel Nikko Naha"
    # And the HotelResult projection surfaces English with the Japanese original kept.
    hr = _to_hotel(hotel_dict)
    assert hr.name == "Hotel Nikko Naha"
    assert hr.originalName == "ホテル日航那覇"


# ---------------------------------------------------------------------------
# No cache poisoning on partial / malformed LLM responses (review #1)
# ---------------------------------------------------------------------------
def test_missing_idx_is_not_cached(cache_db, gate_on):
    """LLM omits a hotel's idx → that hotel falls back to JA and is NOT cached.

    Guards the poisoning bug: a Japanese fallback must never be persisted, so a
    later fetch re-attempts translation once OpenAI recovers.
    """
    hotels = [_ja_hotel(hotel_id=1, name="ホテルA"), _ja_hotel(hotel_id=2, name="ホテルB")]
    # LLM returns a translation for idx 0 only; idx 1 is omitted.
    client = _fake_client([_en(idx=0, name_en="Hotel A")])
    with patch.object(translation_service, "_get_client", return_value=client):
        translation_service.translate_hotels(hotels)
    # Hotel 1 translated + cached; hotel 2 fell back to JA and was NOT cached.
    assert hotels[0]["name_en"] == "Hotel A"
    assert hotels[1]["name_en"] == "ホテルB"
    assert translation_service._get_cached(["1"]) != {}
    assert translation_service._get_cached(["2"]) == {}


def test_omitted_idx_is_reattempted_on_next_call(cache_db, gate_on):
    """Because the miss wasn't cached, a second call hits the LLM again for it."""
    hotels = [_ja_hotel(hotel_id=2, name="ホテルB")]
    empty = _fake_client([])  # returns [] → no translation for idx 0
    with patch.object(translation_service, "_get_client", return_value=empty):
        translation_service.translate_hotels(hotels)
    assert hotels[0]["name_en"] == "ホテルB"  # JA fallback, uncached
    # Second attempt: OpenAI "recovers" and returns a real translation.
    hotels2 = [_ja_hotel(hotel_id=2, name="ホテルB")]
    good = _fake_client([_en(idx=0, name_en="Hotel B")])
    with patch.object(translation_service, "_get_client", return_value=good):
        translation_service.translate_hotels(hotels2)
    good.chat.completions.create.assert_called_once()  # re-attempted, not a cache hit
    assert hotels2[0]["name_en"] == "Hotel B"


def test_string_idx_is_handled_and_cached(cache_db, gate_on):
    """An idx returned as a numeric string ("0") is coerced and maps correctly."""
    hotels = [_ja_hotel(hotel_id=5, name="ホテルC")]
    client = _fake_client([{"idx": "0", "name_en": "Hotel C", "detail_en": "", "location_en": ""}])
    with patch.object(translation_service, "_get_client", return_value=client):
        translation_service.translate_hotels(hotels)
    assert hotels[0]["name_en"] == "Hotel C"
    assert translation_service._get_cached(["5"]) != {}


def test_empty_name_en_is_not_cached(cache_db, gate_on):
    """A matched entry with an empty name_en is a non-translation → JA, uncached."""
    hotels = [_ja_hotel(hotel_id=7, name="ホテルD")]
    client = _fake_client([{"idx": 0, "name_en": "", "detail_en": "x", "location_en": "y"}])
    with patch.object(translation_service, "_get_client", return_value=client):
        translation_service.translate_hotels(hotels)
    assert hotels[0]["name_en"] == "ホテルD"
    assert translation_service._get_cached(["7"]) == {}


def test_nonlist_response_falls_back_all_and_caches_none(cache_db, gate_on):
    """A malformed (non-list) LLM response → whole chunk JA, nothing cached."""
    hotels = [_ja_hotel(hotel_id=8, name="ホテルE")]
    client = _fake_client({"unexpected": "dict"})  # JSON object, not an array
    with patch.object(translation_service, "_get_client", return_value=client):
        translation_service.translate_hotels(hotels)
    assert hotels[0]["name_en"] == "ホテルE"
    assert translation_service._get_cached(["8"]) == {}


# ---------------------------------------------------------------------------
# A mid-flight failure must not clobber already-applied cache HITS (review #2)
# ---------------------------------------------------------------------------
def test_cache_hit_survives_miss_translation_failure(cache_db, gate_on):
    """One cached hotel + one miss whose translation errors: the hit stays English."""
    translation_service._store(
        [{"hotel_id": "100", "name_en": "Cached English", "hotel_special_en": None, "location_en": None}]
    )
    hotels = [_ja_hotel(hotel_id=100, name="キャッシュ"), _ja_hotel(hotel_id=200, name="ミス")]
    failing = _fake_client([])
    failing.chat.completions.create.side_effect = RuntimeError("OpenAI down")
    with patch.object(translation_service, "_get_client", return_value=failing):
        translation_service.translate_hotels(hotels)
    # The cache hit is NOT overwritten to Japanese; only the miss falls back.
    assert hotels[0]["name_en"] == "Cached English"
    assert hotels[1]["name_en"] == "ミス"


# ---------------------------------------------------------------------------
# Chunking oversized batches (review #3)
# ---------------------------------------------------------------------------
def test_oversized_batch_is_chunked_into_multiple_calls(cache_db, gate_on, monkeypatch):
    """More misses than the chunk size → multiple independent LLM calls."""
    monkeypatch.setattr(translation_service, "_TRANSLATE_CHUNK_SIZE", 2)
    hotels = [_ja_hotel(hotel_id=i, name=f"ホテル{i}") for i in range(5)]
    # Each chunk is a fresh payload starting at idx 0; return idx 0 + 1 translations.
    client = MagicMock()

    def _make_resp(*args, **kwargs):
        sent = json.loads(kwargs["messages"][1]["content"])
        content = json.dumps(
            [{"idx": item["idx"], "name_en": f"EN {item['name']}", "detail_en": "", "location_en": ""}
             for item in sent],
            ensure_ascii=False,
        )
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = content
        return resp

    client.chat.completions.create.side_effect = _make_resp
    with patch.object(translation_service, "_get_client", return_value=client):
        translation_service.translate_hotels(hotels)
    # 5 hotels / chunk-size 2 → 3 calls (2 + 2 + 1).
    assert client.chat.completions.create.call_count == 3
    assert [h["name_en"] for h in hotels] == [f"EN ホテル{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# Wiring — the /chat workflow hotel branch produces English (review #4)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_chat_workflow_hotel_branch_returns_english(cache_db, gate_on):
    from agent.workflow import pipeline
    from agent.workflow.intent import Intent

    intent = Intent(prefecture="Okinawa", query="onsen ryokan", wants_hotels=True)
    records = [
        {"name": "Yamada Onsen", "location": "Naha", "spring_type": "Sulfur Spring",
         "spa_quality": "desc", "lat": 26.2124, "lng": 127.6809}
    ]
    fake = _fake_client([_en(name_en="Hotel Nikko Naha", location_en="Naha, Okinawa")])
    with patch.object(pipeline, "parse_intent", new=AsyncMock(return_value=intent)), \
         patch.object(pipeline, "query_onsen_structured", return_value=records), \
         patch.object(pipeline, "search_hotels", return_value=[_ja_hotel()]), \
         patch.object(pipeline, "get_history", return_value=[]), \
         patch.object(pipeline, "save_message"), \
         patch.object(translation_service, "_get_client", return_value=fake):
        result = await pipeline.run_workflow("onsen with hotels in Okinawa", "s-chat")

    hotel = result["hotels"][0]
    assert hotel["name"] == "Hotel Nikko Naha"       # English visible name
    assert hotel["originalName"] == "ホテル日航那覇"    # Japanese kept
