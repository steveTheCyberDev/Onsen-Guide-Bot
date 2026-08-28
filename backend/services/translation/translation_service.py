"""Hotel name/detail JA→EN translation with a persistent per-hotel cache.

Rakuten Travel returns hotel name / ``hotelSpecial`` / address in Japanese only, so
without this the map's hotel cards surface Japanese text to English-speaking users.
This service translates those three fields to English ONCE per hotel and caches the
result by Rakuten hotel id, so a given hotel is only ever paid-for once.

Design (all load-bearing):

- **Framework-agnostic** — like the ingest translate-at-ingest step it calls the
  OpenAI client directly (``openai.OpenAI``); it imports NOTHING from ``agent/`` or
  ``api/``, so the ``/hotels`` route, the ``/chat`` workflow, and the trip planner can
  all use it without a layering violation.
- **Cache-by-id, persistent** — reuses the SAME SQLAlchemy-Core pattern as the Step-0
  chat session store (``services/chat/chat_service.py``): a ``hotel_translations``
  table keyed by ``hotel_id``. SQLite locally, Postgres in prod — the dialect is
  chosen entirely by ``settings.resolved_hotel_translation_db_url`` (defaults to the
  session DB, splittable in prod). Only cache MISSES hit the LLM.
- **Batched + chunked** — misses are translated in gpt-4o-mini requests of at most
  ``_TRANSLATE_CHUNK_SIZE`` hotels each (mirrors ``scripts/ingest.py::translate_batch``),
  so a big multi-stop trip can't overflow a single response and truncate the JSON.
- **Fail-soft, no cache poisoning** — ANY error (LLM, JSON parse, DB) falls the
  affected hotels back to the original Japanese text and logs; the response is NEVER
  broken. Crucially, a hotel is cached ONLY when the LLM genuinely translated it (a
  real ``name_en``); a Japanese fallback is never persisted, so once OpenAI recovers
  that hotel is re-attempted rather than being a permanent Japanese cache hit.

Gated behind ``settings.hotel_translation_enabled`` (default OFF): when off,
:func:`translate_hotels` is a pure no-op — no LLM call, no DB touch, Japanese
passthrough exactly as today.

Public seam::

    translate_hotels(hotels: list[dict]) -> list[dict]

It enriches each hotel dict IN PLACE with ``name_en`` / ``hotelSpecial_en`` /
``location_en`` (and returns the same list). Callers read the ``*_en`` field with a
fallback to the Japanese original (``h.get("name_en") or h.get("name")``), so the
gate-off / fail-soft paths degrade cleanly to Japanese.
"""

import json
import logging

import openai
from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    insert,
    select,
)
from sqlalchemy.engine import Engine

from core.config import settings

logger = logging.getLogger(__name__)

# Max hotels translated per LLM request. Chunking bounds the output-token size of a
# single batched call so a big multi-stop trip (up to N×10 hotels) can't overflow the
# response and truncate the JSON (which would fail-soft the whole payload to
# Japanese). Each chunk is translated + persisted independently and fails soft alone.
_TRANSLATE_CHUNK_SIZE = 25

metadata = MetaData()

# One row per Rakuten hotel. The primary key is the Rakuten hotel id (stringified —
# hotelNo is an integer in the API but we store it as text so the key type is stable
# across dialects). English fields are nullable because a source field can be
# empty/absent (e.g. a hotel with no hotelSpecial). ``created_at`` is recorded so a
# future TTL sweep could expire stale rows; not needed today (fresh rows are only
# written for genuine translations, never Japanese fallbacks).
hotel_translations = Table(
    "hotel_translations",
    metadata,
    Column("hotel_id", String, primary_key=True),
    Column("name_en", Text, nullable=True),
    Column("hotel_special_en", Text, nullable=True),
    Column("location_en", Text, nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
)

# Module-level engine singleton, built lazily on first use so importing this module
# has no side effects and tests can repoint settings before the engine is created.
# Backed by resolved_hotel_translation_db_url (defaults to the chat session DB; may
# be split onto its own DB in prod).
_engine: Engine | None = None

# Lazily-built OpenAI client (see _get_client). Not built at import so the module
# imports cleanly under a dummy key in tests, and so tests can mock the client.
_client: openai.OpenAI | None = None


def _get_engine() -> Engine:
    """Return the process-wide engine, creating it + the table on first use.

    ``metadata.create_all`` is idempotent, so a fresh local SQLite file works with
    zero setup and an existing DB is left untouched. Mirrors
    ``chat_service._get_engine``.
    """
    global _engine
    if _engine is None:
        url = settings.resolved_hotel_translation_db_url
        _engine = create_engine(url, future=True)
        metadata.create_all(_engine)
        # Log the dialect, never the full URL (a Postgres URL carries credentials).
        logger.info("hotel translation cache initialized backend=%s", _engine.dialect.name)
    return _engine


def _reset_engine_for_tests() -> None:
    """Dispose and clear the engine singleton so the next call rebuilds it.

    Used by the test suite to repoint the cache DB at a throwaway SQLite file
    between tests. Not used in production code. Mirrors
    ``chat_service._reset_engine_for_tests``.
    """
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def _get_client() -> openai.OpenAI:
    """Return the lazily-built OpenAI client (mirrors the ingest scripts)."""
    global _client
    if _client is None:
        _client = openai.OpenAI(api_key=settings.openai_api_key)
    return _client


def _get_cached(ids: list[str]) -> dict[str, dict]:
    """Look up cached translations for the given hotel ids in one query.

    Returns ``{hotel_id -> {name_en, hotel_special_en, location_en}}`` for the ids
    that are present in the cache; absent ids are simply omitted (they are misses).
    """
    if not ids:
        return {}
    stmt = select(
        hotel_translations.c.hotel_id,
        hotel_translations.c.name_en,
        hotel_translations.c.hotel_special_en,
        hotel_translations.c.location_en,
    ).where(hotel_translations.c.hotel_id.in_(ids))
    engine = _get_engine()
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    return {
        row.hotel_id: {
            "name_en": row.name_en,
            "hotel_special_en": row.hotel_special_en,
            "location_en": row.location_en,
        }
        for row in rows
    }


def _store(entries: list[dict]) -> None:
    """Persist freshly translated rows, skipping any id already present.

    ``entries`` is a list of ``{hotel_id, name_en, hotel_special_en, location_en}``.
    A dialect-agnostic exists-then-insert (rather than an ON CONFLICT upsert) keeps
    this identical on SQLite and Postgres; the surrounding transaction makes the
    check-then-insert atomic for a single writer. Mirrors
    ``chat_service.save_message``. Callers only ever pass GENUINE translations here
    (never Japanese fallbacks) — see :func:`_translate_chunk`.
    """
    if not entries:
        return
    engine = _get_engine()
    with engine.begin() as conn:
        existing = {
            row.hotel_id
            for row in conn.execute(
                select(hotel_translations.c.hotel_id).where(
                    hotel_translations.c.hotel_id.in_([e["hotel_id"] for e in entries])
                )
            ).all()
        }
        fresh = [e for e in entries if e["hotel_id"] not in existing]
        if fresh:
            conn.execute(insert(hotel_translations), fresh)


def _translate_batch(payload: list[dict]) -> object:
    """Translate a batch of hotels' name/detail/location in ONE gpt-4o-mini call.

    ``payload`` items are ``{"idx", "name", "detail", "location"}`` (Japanese source).
    Returns the parsed JSON (expected: an array of ``{"idx", "name_en", "detail_en",
    "location_en"}`` with the SAME idx values). Mirrors
    ``scripts/ingest.py::translate_batch`` (JSON in/out, temperature 0). Raises on any
    OpenAI/JSON error — the caller turns that into the Japanese fail-soft fallback.
    """
    content = json.dumps(payload, ensure_ascii=False)
    response = _get_client().chat.completions.create(
        model=settings.hotel_translation_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You translate Japanese hotel listings into natural English for "
                    "English-speaking travellers. Return ONLY a JSON array with the "
                    "same ids. Each object must have: idx (unchanged integer), name_en "
                    "(English/romanised hotel name), detail_en (natural English of the "
                    "detail/selling-point text), location_en (English city/prefecture). "
                    "If a source field is empty, return an empty string for it. "
                    "Output only valid JSON, no markdown."
                ),
            },
            {"role": "user", "content": content},
        ],
        temperature=0,
    )
    return json.loads(response.choices[0].message.content)


def _index_translations(translations: object) -> dict[int, dict]:
    """Index the LLM's translation objects by a normalized INTEGER idx.

    The model can return idx as an int, a numeric string ("0"), or omit it entirely,
    and can drop or renumber entries. We coerce each idx to int where possible and
    drop anything unparseable, so a valid entry still maps to the right hotel. A
    non-list response (e.g. the LLM returned a dict or a list of strings) yields an
    empty index → every hotel in the chunk fails soft to Japanese and none is cached.
    First half of the no-cache-poisoning guard (see :func:`_translate_chunk`).
    """
    by_idx: dict[int, dict] = {}
    if not isinstance(translations, list):
        return by_idx
    for t in translations:
        if not isinstance(t, dict):
            continue
        try:
            key = int(t.get("idx"))
        except (TypeError, ValueError):
            continue
        by_idx[key] = t
    return by_idx


def _apply_japanese_fallback(hotels: list[dict]) -> None:
    """Set each hotel's ``*_en`` fields to its Japanese source (fail-soft path).

    Explicit so a failed/absent translation is observable in the output shape and
    callers reading ``name_en`` still get a (Japanese) value rather than None. A
    hotel that goes through this is NEVER cached, so it is re-attempted next time.
    """
    for h in hotels:
        h["name_en"] = h.get("name")
        h["hotelSpecial_en"] = h.get("hotelSpecial")
        h["location_en"] = h.get("address")


def _real_translation(t: dict | None, h: dict) -> dict | None:
    """Return the English fields to apply IFF the LLM genuinely translated the name.

    A hotel counts as a real, cacheable hit only when its matched translation exists
    and carries a non-empty ``name_en``. An empty/missing ``name_en`` means the LLM
    didn't usefully translate this row → return None so the caller falls it back to
    Japanese and does NOT cache it (#1). ``detail``/``location`` may legitimately be
    empty (empty source text), so they fall back to the Japanese source without
    disqualifying the hit.
    """
    if not t:
        return None
    name_en = (t.get("name_en") or "").strip()
    if not name_en:
        return None
    return {
        "name_en": name_en,
        "hotelSpecial_en": t.get("detail_en") or h.get("hotelSpecial"),
        "location_en": t.get("location_en") or h.get("address"),
    }


def _translate_chunk(chunk: list[dict]) -> None:
    """Translate one chunk of MISS hotels; fail-soft and poison-free.

    Steps: build the payload → one LLM call → map results back by normalized idx.
    A whole-chunk error (LLM/JSON) falls the entire chunk back to Japanese and caches
    nothing. Per hotel, only a GENUINE translation (a real, non-empty ``name_en``
    matched to this hotel's idx) is applied AND cached; a missing/renumbered idx or an
    empty name_en falls that one hotel back to Japanese and is NOT cached — so OpenAI
    recovery re-attempts it instead of the id becoming a permanent Japanese hit (#1).
    Only touches the MISS hotels handed to it — cache hits are never seen here (#2).
    """
    payload = [
        {
            "idx": i,
            "name": h.get("name") or "",
            "detail": h.get("hotelSpecial") or "",
            "location": h.get("address") or "",
        }
        for i, h in enumerate(chunk)
    ]
    try:
        by_idx = _index_translations(_translate_batch(payload))
    except Exception as e:
        logger.warning(
            "hotel translation LLM call failed (%d hotels) — falling back to "
            "Japanese: %s", len(chunk), e,
        )
        _apply_japanese_fallback(chunk)
        return

    to_store: list[dict] = []
    for i, h in enumerate(chunk):
        applied = _real_translation(by_idx.get(i), h)
        if applied is None:
            # No usable translation for this hotel — Japanese fallback, NOT cached.
            _apply_japanese_fallback([h])
            continue
        h["name_en"] = applied["name_en"]
        h["hotelSpecial_en"] = applied["hotelSpecial_en"]
        h["location_en"] = applied["location_en"]
        if h.get("id") is not None:
            to_store.append(
                {
                    "hotel_id": str(h["id"]),
                    "name_en": h["name_en"],
                    "hotel_special_en": h["hotelSpecial_en"],
                    "location_en": h["location_en"],
                }
            )

    # Best-effort persist of ONLY the genuine translations — a write failure must not
    # break the response (translations are already applied above).
    try:
        _store(to_store)
    except Exception as e:
        logger.warning("hotel translation cache write failed: %s", e)


def translate_hotels(hotels: list[dict]) -> list[dict]:
    """Translate hotel name/details JA→EN, cache-aware and fail-soft (in place).

    Enriches each hotel dict with ``name_en`` / ``hotelSpecial_en`` / ``location_en``
    and returns the same list. Cache HITS (by ``hotel['id']``) skip the LLM; only
    MISSES are translated, in chunked batched requests. Hotels without an id are still
    translated but not cached (their translation can't be keyed).

    Gate: when ``settings.hotel_translation_enabled`` is False (default) this is a
    pure no-op — no LLM call, no DB touch, no keys added — so callers see today's
    Japanese passthrough exactly.

    Fail-soft: a cache-read/engine-build failure falls back ALL hotels to Japanese
    (nothing has been enriched yet). Once past that, cache hits are enriched and the
    misses are translated in independently fail-soft chunks — a chunk failure only
    falls back that chunk's misses and NEVER clobbers an already-enriched cache hit
    (#2). A Japanese fallback is never cached (#1). Never raises.

    Args:
        hotels: Raw hotel dicts from ``rakuten_service.search_hotels`` (each may carry
            an ``id`` = Rakuten hotelNo, plus ``name`` / ``hotelSpecial`` / ``address``).

    Returns:
        The same list, with translations attached (or Japanese fallbacks on error /
        gate-off — in the gate-off case, no keys are added at all).
    """
    if not settings.hotel_translation_enabled:
        # Clean no-op: today's behaviour exactly. No keys added; callers fall back to
        # the Japanese source fields.
        return hotels
    if not hotels:
        return hotels

    # Pre-partition: read the cache (this also lazily builds the engine). Reserved for
    # this guard — nothing is enriched yet, so a failure here safely falls ALL back to
    # Japanese without clobbering any hit.
    try:
        ids = [str(h["id"]) for h in hotels if h.get("id") is not None]
        cached = _get_cached(ids)
    except Exception as e:
        logger.warning(
            "hotel translation cache read failed — falling back to Japanese: %s", e
        )
        _apply_japanese_fallback(hotels)
        return hotels

    # Partition into cache hits (enriched now) and misses (translated below). Pure
    # dict ops — cannot raise — so hits enriched here are safe from any later fallback.
    miss_hotels: list[dict] = []
    for h in hotels:
        hid = str(h["id"]) if h.get("id") is not None else None
        if hid is not None and hid in cached:
            hit = cached[hid]
            h["name_en"] = hit["name_en"] or h.get("name")
            h["hotelSpecial_en"] = hit["hotel_special_en"] or h.get("hotelSpecial")
            h["location_en"] = hit["location_en"] or h.get("address")
        else:
            miss_hotels.append(h)

    logger.info(
        "hotel translation | total=%d hits=%d misses=%d",
        len(hotels), len(hotels) - len(miss_hotels), len(miss_hotels),
    )

    # Translate misses in independent, individually fail-soft chunks (#3). This step
    # only ever touches the MISS hotels — the cache hits above are never re-visited.
    for start in range(0, len(miss_hotels), _TRANSLATE_CHUNK_SIZE):
        _translate_chunk(miss_hotels[start : start + _TRANSLATE_CHUNK_SIZE])

    return hotels
