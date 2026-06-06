"""Deterministic V2 onsen workflow — the ``run_workflow`` pipeline.

Replaces the ReAct agent's expensive routing loop with an explicit ``async def``
pipeline that ties together the already-merged Step 1 (``query_onsen_structured``)
and Step 2 (``parse_intent``):

    run_workflow(message)
      ① parse_intent(message)          LLM (small)  → {prefecture, query, wants_hotels}
      ② query_onsen_structured(...)    Python       → onsens[]  (no LLM; kills fabrication)
      ⑤ analyze_onsen(...)             DEFERRED     → gated seam only (see TODO below)
      ③ if wants_hotels and onsens:    code branch  → search_hotels (passthrough)
      ④ reply = template               no LLM

The DATA layer (onsens[], hotels[]) is assembled in pure Python from Chroma
metadata and the Rakuten service, so there is no LLM round-trip that could
fabricate facts. The only LLM call is the small intent-parse hop.

The response contract is IDENTICAL to ``run_agent`` (reply, onsens[], hotels[])
so ``api/routes/chat.py`` and the frontend work unchanged — a clean A/B against
the ReAct baseline. ``run_workflow`` is NOT wired into the API yet (that's the
``chat_engine`` flag step); for now it is reachable only by tests.
"""

import asyncio
import logging

from agent.agent import AgentResponse, HotelResult, OnsenResult
from agent.workflow.intent import parse_intent
from services.chat.chat_service import get_history, save_message
from services.rakuten.rakuten_service import search_hotels
from services.retrieval.retrieval_service import query_onsen_structured

logger = logging.getLogger(__name__)

# Keys on the query_onsen_structured records that OnsenResult accepts. The
# records carry EXTRA keys (description, detail_url) that are NOT fields on
# OnsenResult; since OnsenResult forbids extras (Pydantic v2 default),
# OnsenResult(**record) would raise. We project onto this allow-list instead.
_ONSEN_FIELDS = ("name", "location", "spring_type", "spa_quality", "sales_point", "lat", "lng")

# --- LangSmith tracing (import-guarded, no-op when disabled) ---
# Wrap run_workflow with langsmith's @traceable so the V2 workflow run is
# distinguishable from the v1-baseline ReAct run in the LangSmith UI. Tracing
# only actually emits when the LangSmith env vars are exported (see
# core.config.export_langsmith_env, called at agent import time); otherwise the
# decorator is a transparent pass-through. The import guard keeps the module
# importable even if langsmith is ever absent.
try:
    from langsmith import traceable

    _trace = traceable(
        run_name="chat-workflow",
        tags=["chat", "workflow"],
        metadata={
            "endpoint": "/chat",
            "agent_type": "workflow",
            "version": "v2-workflow",
        },
    )
except Exception:  # pragma: no cover - langsmith always present in this project

    def _trace(fn):
        return fn


def _build_onsens(records: list[dict]) -> list[OnsenResult]:
    """Project structured Chroma records onto OnsenResult.

    Records contain extra keys (description, detail_url) not on OnsenResult, so
    we pass only the accepted fields rather than ``OnsenResult(**record)``.
    """
    return [
        OnsenResult(**{k: r.get(k) for k in _ONSEN_FIELDS})
        for r in records
    ]


def _to_hotel(h: dict) -> HotelResult:
    """Map a Rakuten service hotel dict to a HotelResult (V1 passthrough).

    Mirrors ``api/routes/hotels.py::_to_item`` field-for-field so /chat and
    /hotels produce identical hotel shapes. Rakuten returns Japanese-only names
    (no translation in V1), so name == originalName == the Japanese string.
    HotelResult has no ``distance`` field, so distance is not computed here.
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


def _build_reply(prefecture: str | None, onsens: list[OnsenResult], hotels: list[HotelResult]) -> str:
    """Build the template reply (no LLM). Preserves the no-result UX."""
    where = prefecture or "Japan"
    if not onsens:
        return f"No onsen found in {where} matching your query."
    reply = f"Found {len(onsens)} onsen in {where}"
    if hotels:
        hotel_noun = "hotel" if len(hotels) == 1 else "hotels"
        reply += f" and {len(hotels)} nearby {hotel_noun}"
    reply += "."
    return reply


@_trace
async def run_workflow(message: str, session_id: str) -> dict:
    """Run the deterministic V2 onsen workflow.

    Mirrors ``run_agent``'s signature and return shape (reply, onsens[],
    hotels[]) so callers and the API contract are unchanged.

    Args:
        message: The latest user message.
        session_id: Conversation/session identifier for history + persistence.

    Returns:
        ``AgentResponse.model_dump()`` — the same dict shape as ``run_agent``.
    """
    logger.info("run_workflow | session_id=%s", session_id)

    # ① Intent — the only LLM call (small/cheap intent_model).
    history = get_history(session_id)
    intent = await parse_intent(message, history)

    # ② Retrieval — pure Python, no LLM, no fabrication.
    records = query_onsen_structured(intent.query, prefecture=intent.prefecture)
    onsens = _build_onsens(records)
    logger.info("run_workflow | retrieved onsens=%d", len(onsens))

    # ⑤ Analyze seam — DEFERRED. Resequenced 2026-06-06 to the end of the
    # pipeline; not implemented here. When the guide layer lands it fills in
    # the per-onsen pros/cons + recommendation here, gated off by default.
    # TODO(analyze_onsen): guide pros/cons layer, gated off by default — fills in here.

    # ③ Hotels — conditional passthrough. Use the first onsen that has BOTH
    # coordinates; if none has coords, skip (no per-request geocoding).
    hotels: list[HotelResult] = []
    if intent.wants_hotels and onsens:
        coords = next(
            ((o.lat, o.lng) for o in onsens if o.lat is not None and o.lng is not None),
            None,
        )
        if coords is not None:
            lat, lng = coords
            # search_hotels is sync (uses requests) — run off the event loop.
            raw = await asyncio.to_thread(search_hotels, lat, lng)
            hotels = [_to_hotel(h) for h in raw]
            logger.info("run_workflow | hotels=%d (lat=%s lng=%s)", len(hotels), lat, lng)
        else:
            logger.warning("run_workflow | wants_hotels but no onsen has coords — skipping hotels")

    # ④ Reply — template, no LLM.
    reply = _build_reply(intent.prefecture, onsens, hotels)

    save_message(session_id, message, reply)
    return AgentResponse(reply=reply, onsens=onsens, hotels=hotels).model_dump()
