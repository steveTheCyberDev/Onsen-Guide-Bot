"""Deterministic onsen workflow — the ``run_workflow`` pipeline.

The ONLY /chat engine: an explicit ``async def`` pipeline that ties together the
intent parse and the structured retrieval (no autonomous routing loop):

    run_workflow(message)
      ① parse_intent(message)          LLM (small)  → {prefecture, query, wants_hotels}
      ② query_onsen_structured(...)    Python       → onsens[]  (no LLM; kills fabrication)
      ⑤ analyze_onsen(...)             gated        → recommend-mode brain (analyze_enabled)
      ③ if wants_hotels and onsens:    code branch  → search_hotels (passthrough)
      ④ reply = template               no LLM

The DATA layer (onsens[], hotels[]) is assembled in pure Python from Chroma
metadata and the Rakuten service, so there is no LLM round-trip that could
fabricate facts. The only LLM call on the search path is the small intent-parse hop.

``run_workflow`` is called directly by ``api/routes/chat.py``; its return shape is
``AgentResponse.model_dump()`` (reply, onsens[], hotels[], recommendation).
"""

import asyncio
import logging
import time

from langchain_core.callbacks import UsageMetadataCallbackHandler

from agent.schemas import AgentResponse, HotelResult, OnsenResult
from agent.trip.agent import plan_trip
from agent.workflow.analyze import analyze_onsen
from agent.workflow.ask import answer_question
from agent.workflow.cost import summarize_usage
from agent.workflow.intent import parse_intent
from core.config import export_langsmith_env, settings
from services.chat.chat_service import get_history, save_message
from services.rakuten.rakuten_service import search_hotels
from services.translation.translation_service import translate_hotels
from services.retrieval.retrieval_service import (
    known_prefectures,
    query_onsen_structured,
)

logger = logging.getLogger(__name__)

# ask-mode placeholder reply. Layer 2 (semantic RAG over knowledge docs) is a
# later V2.5 chunk; until then ask-mode returns this rather than a result set.
_ASK_STUB_REPLY = (
    "Onsen knowledge Q&A is coming soon — for now I can help you find or "
    "recommend onsen."
)

# Keys on the query_onsen_structured records that OnsenResult accepts. The
# records carry EXTRA keys (description, detail_url) that are NOT fields on
# OnsenResult. Pydantic v2's DEFAULT is extra="ignore" (unknown keys are silently
# dropped, not rejected), but OnsenResult sets extra="forbid" EXPLICITLY
# (agent/schemas.py, defense-in-depth), so OnsenResult(**record) with those extra
# keys would raise ValidationError. We project onto this allow-list instead.
_ONSEN_FIELDS = ("name", "location", "spring_type", "spa_quality", "lat", "lng")

# Default/ceiling for how many onsen retrieval returns. Used when the user names
# no count, and as the upper clamp when they do (e.g. 'top 100' → _MAX_RESULTS).
_MAX_RESULTS = 20

# Export LangSmith tracing env vars (if enabled) BEFORE importing/constructing the
# @traceable decorator below. langsmith reads these env vars and caches them, so
# they must be present in os.environ before the first langsmith import fires. This
# is the live engine module, imported on the /chat path via api/routes/chat.py, so
# it is the right home for the export now that the ReAct agent module is gone.
# No-op + tracing disabled unless LANGSMITH_TRACING=true and an API key are set.
_TRACING_ENABLED = export_langsmith_env()
if _TRACING_ENABLED:
    logger.info(
        "LangSmith tracing ENABLED | project=%s | endpoint=%s",
        settings.langsmith_project,
        settings.langsmith_endpoint,
    )
else:
    logger.info("LangSmith tracing disabled (no-op)")

# --- LangSmith tracing (import-guarded, no-op when disabled) ---
# Wrap run_workflow with langsmith's @traceable so the workflow run is grouped and
# labelled in the LangSmith UI. Tracing only actually emits when the LangSmith env
# vars are exported (see the export_langsmith_env call just above); otherwise the
# decorator is a transparent pass-through. The import guard keeps the module
# importable even if langsmith is ever absent.
try:
    from langsmith import traceable

    _trace = traceable(
        run_name="chat-workflow",
        tags=["chat", "workflow", f"env:{settings.app_env}"],
        metadata={
            "endpoint": "/chat",
            "agent_type": "workflow",
            # `version` labels the engine variant; `app_version` is the deploy id
            # (git SHA/tag) — a different axis. settings is process-static, so
            # reading it at decorator/import time is fine.
            "version": "v2-workflow",
            "environment": settings.app_env,
            "app_version": settings.app_version,
        },
    )
except Exception:  # pragma: no cover - langsmith always present in this project

    def _trace(fn):
        return fn


# Current-run accessor, guarded the same way as `_trace`. Unlike the static
# decorator metadata above, this lets us attach PER-REQUEST values (mode, cost,
# tokens) to the live run tree from inside run_workflow. When langsmith is absent
# the name resolves to a no-op returning None, so the attach path below is inert.
try:
    from langsmith import get_current_run_tree
except Exception:  # pragma: no cover - langsmith always present in this project

    def get_current_run_tree():
        return None


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
    """Map a Rakuten service hotel dict to a HotelResult.

    Mirrors ``api/routes/hotels.py::_to_item`` field-for-field so /chat and
    /hotels produce identical hotel shapes. When hotel translation is enabled the
    dict carries ``name_en`` / ``hotelSpecial_en`` / ``location_en`` (added by
    ``translate_hotels`` in the hotel branch below); we surface those and keep the
    Japanese name in ``originalName``. Each ``*_en`` read falls back to the Japanese
    source, so gate-off / fail-soft shows Japanese exactly as before. HotelResult
    has no ``distance`` field, so distance is not computed here.
    """
    name = h.get("name") or ""
    price = h.get("price")
    return HotelResult(
        name=h.get("name_en") or name,
        originalName=name,
        location=h.get("location_en") or h.get("address"),
        hotelSpecial=h.get("hotelSpecial_en") or h.get("hotelSpecial"),
        price=str(price) if price is not None else None,
        image=h.get("hotelImageUrl"),
        url=h.get("url"),
        lat=h.get("lat"),
        lng=h.get("lng"),
    )


def _safe_location_label(prefecture: str | None) -> str:
    """Return a location label safe to interpolate into the user-facing reply.

    Only echoes ``prefecture`` when it matches an INGESTED prefecture (the
    ``known_prefectures()`` allow-list, matched case-insensitively); anything else
    — including a jailbroken/injected intent parse that smuggled attacker text into
    ``intent.prefecture`` — is generalized to ``"Japan"`` so unvalidated text never
    reaches the reply (reflected-echo hardening). Legitimate real prefectures render
    unchanged, in their canonical ingested casing.
    """
    if not prefecture:
        return "Japan"
    canonical = {p.lower(): p for p in known_prefectures()}
    return canonical.get(prefecture.strip().lower(), "Japan")


def _build_reply(prefecture: str | None, onsens: list[OnsenResult], hotels: list[HotelResult]) -> str:
    """Build the template reply (no LLM). Preserves the no-result UX."""
    where = _safe_location_label(prefecture)
    if not onsens:
        return f"No onsen found in {where} matching your query."
    reply = f"Found {len(onsens)} onsen in {where}"
    if hotels:
        hotel_noun = "hotel" if len(hotels) == 1 else "hotels"
        reply += f" and {len(hotels)} nearby {hotel_noun}"
    reply += "."
    return reply


def _attach_cost_to_trace(mode: str, summary: dict) -> None:
    """Attach per-request mode/cost/token fields to the active LangSmith run.

    Mutates the CURRENT run tree's metadata + tags so cost can be sliced by mode
    (search|recommend|ask) in LangSmith and cross-checked against LangSmith's own
    cost estimate. LangSmith flushes the run tree on run end, so mutating it here
    inside ``run_workflow`` is sufficient.

    FULLY fail-safe: when langsmith is absent or tracing is disabled,
    ``get_current_run_tree()`` returns None and this is a no-op; any unexpected
    error is swallowed so trace bookkeeping never leaks into the request path.
    """
    try:
        rt = get_current_run_tree()
        if rt is None:
            return
        rt.metadata.update(
            {
                "mode": mode,
                "cost_usd": summary["cost_usd"],
                "input_tokens": summary["input_tokens"],
                "output_tokens": summary["output_tokens"],
                "models": ",".join(summary["models"]) or "none",
            }
        )
        rt.tags = (rt.tags or []) + [f"mode:{mode}"]
    except Exception:  # pragma: no cover - defensive; trace must never break /chat
        logger.debug("failed to attach cost metadata to langsmith run", exc_info=True)


def _log_cost(
    session_id: str,
    mode: str,
    usage_cb: UsageMetadataCallbackHandler,
    started: float,
) -> None:
    """Emit one structured cost/token line per /chat from the workflow.

    Summarizes the request's token usage (captured by ``usage_cb`` across the
    intent + analyze, or intent + ask, LLM calls) into models used, token totals, estimated USD
    cost, and end-to-end latency. Also attaches mode/cost/tokens to the active
    LangSmith run so cost is sliceable by mode in the trace UI.
    """
    summary = summarize_usage(usage_cb.usage_metadata)
    _attach_cost_to_trace(mode, summary)
    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "workflow_cost | session_id=%s | mode=%s | models=%s | input_tokens=%d | "
        "output_tokens=%d | cost_usd=%.6f | latency_ms=%d",
        session_id,
        mode,
        ",".join(summary["models"]) or "none",
        summary["input_tokens"],
        summary["output_tokens"],
        summary["cost_usd"],
        latency_ms,
    )


@_trace
async def run_workflow(message: str, session_id: str) -> dict:
    """Run the deterministic onsen workflow — the /chat engine.

    Args:
        message: The latest user message.
        session_id: Conversation/session identifier for history + persistence.

    Returns:
        ``AgentResponse.model_dump()`` — the dict ``api/routes/chat.py`` returns as
        the ChatResponse (reply, onsens[], hotels[], recommendation).
    """
    logger.info("run_workflow | session_id=%s", session_id)

    # One usage callback spans every LLM call in this request (intent + analyze,
    # or intent + ask in ask-mode). The intent/analyze calls use
    # .with_structured_output and the ask call returns a plain string, so usage is
    # NOT on their return values — the callback is the reliable capture point.
    # Cost accounting lives here in the workflow layer, keeping services/
    # LLM-agnostic.
    usage_cb = UsageMetadataCallbackHandler()
    callbacks = [usage_cb]
    started = time.monotonic()

    # ① Intent — small/cheap intent_model. Also classifies the mode.
    history = get_history(session_id)
    intent = await parse_intent(message, history, callbacks=callbacks)

    recommendation: str | None = None

    # ask-mode: Layer 2 semantic RAG over the knowledge docs, gated by
    # ask_enabled (A/B + instant rollback, mirrors analyze_enabled below). When
    # the gate is OFF (default) ask returns the safe stub — prod behavior is
    # exactly as before. When ON, answer_question retrieves KB chunks and writes a
    # grounded answer (or the deterministic no-info fallback). Either way the
    # response shape is identical: empty onsens/hotels, recommendation=None.
    if intent.mode == "ask":
        if settings.ask_enabled:
            # Retrieve with the ORIGINAL message, not intent.query: parse_intent's
            # reformulation is lossy and non-deterministic for prose Q&A (it was
            # designed to extract structured SEARCH terms), and a weaker phrasing
            # can push every KB match past the distance threshold. The raw question
            # is the most reliable semantic-RAG signal.
            reply = await answer_question(message, callbacks=callbacks)
        else:
            reply = _ASK_STUB_REPLY
        onsens: list[OnsenResult] = []
        hotels: list[HotelResult] = []
        _log_cost(session_id, intent.mode, usage_cb, started)
        save_message(session_id, message, reply)
        return AgentResponse(
            reply=reply, onsens=onsens, hotels=hotels, recommendation=recommendation
        ).model_dump()

    # trip-mode: V3 multi-day trip-planner, gated by trip_enabled (A/B + instant
    # rollback, mirrors ask_enabled above). IMPORTANT — unlike ask, the branch is
    # taken ONLY when the gate is ON: `mode == "trip" AND settings.trip_enabled`.
    # When the gate is OFF (prod default) we do NOT early-return; a trip-classified
    # query FALLS THROUGH to the normal retrieval path below so it degrades to a
    # regular onsen search rather than dead-ending. PR2 ships the plan_trip stub
    # (no service/LLM calls); the real agent lands in later PRs.
    if intent.mode == "trip" and settings.trip_enabled:
        response = await plan_trip(message, session_id, callbacks=callbacks)
        _log_cost(session_id, intent.mode, usage_cb, started)
        save_message(session_id, message, response.reply)
        return response.model_dump()

    # ② Retrieval — pure Python, no LLM, no fabrication (search + recommend).
    # Honour an explicit count from the user ('top 5' → 5), clamped to a sane
    # [1, _MAX_RESULTS] range; default to _MAX_RESULTS when no count was asked.
    n_results = (
        max(1, min(intent.limit, _MAX_RESULTS)) if intent.limit else _MAX_RESULTS
    )
    records = query_onsen_structured(
        intent.query, prefecture=intent.prefecture, n_results=n_results
    )
    onsens = _build_onsens(records)
    logger.info(
        "run_workflow | retrieved onsens=%d (n_results=%d)", len(onsens), n_results
    )

    # ⑤ Analyze — RECOMMEND brain. Runs ONLY in recommend mode AND only when the
    # analyze_enabled gate is on (A/B rollout seam). When off, recommend falls
    # back to returning candidates without pros/cons — safe/dead until flipped.
    if intent.mode == "recommend" and settings.analyze_enabled:
        onsens, recommendation = await analyze_onsen(
            intent.query, onsens, callbacks=callbacks
        )

    # ③ Hotels — conditional passthrough. Use the first onsen that has BOTH
    # coordinates; if none has coords, skip (no per-request geocoding).
    hotels = []
    if intent.wants_hotels and onsens:
        coords = next(
            ((o.lat, o.lng) for o in onsens if o.lat is not None and o.lng is not None),
            None,
        )
        if coords is not None:
            lat, lng = coords
            # Fail soft — a Rakuten outage/misconfig must NOT 500 the whole
            # response and lose the onsen recommendation already computed above.
            # On any error, log it and leave hotels=[]; the reply builder
            # handles an empty list. search_hotels is sync (uses requests) —
            # run off the event loop.
            try:
                raw = await asyncio.to_thread(search_hotels, lat, lng)
                # Translate name/details JA→EN (cache-aware, batched). Gated +
                # fail-soft: off = Japanese passthrough (no-op, byte-identical to the
                # old behaviour); any error falls back to Japanese. Keeps /chat
                # consistent with /hotels + trips. Sync (LLM+DB) → off the event loop.
                raw = await asyncio.to_thread(translate_hotels, raw)
                hotels = [_to_hotel(h) for h in raw]
                logger.info("run_workflow | hotels=%d (lat=%s lng=%s)", len(hotels), lat, lng)
            except Exception as e:
                logger.warning(
                    "run_workflow | hotel lookup failed — returning onsens "
                    "without hotels (lat=%s lng=%s): %s",
                    lat,
                    lng,
                    e,
                )
                hotels = []
        else:
            logger.warning("run_workflow | wants_hotels but no onsen has coords — skipping hotels")

    # ④ Reply — template, no LLM.
    reply = _build_reply(intent.prefecture, onsens, hotels)

    _log_cost(session_id, intent.mode, usage_cb, started)
    save_message(session_id, message, reply)
    return AgentResponse(
        reply=reply, onsens=onsens, hotels=hotels, recommendation=recommendation
    ).model_dump()
