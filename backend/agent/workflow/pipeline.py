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
import time

from langchain_core.callbacks import UsageMetadataCallbackHandler

from agent.agent import AgentResponse, HotelResult, OnsenResult
from agent.workflow.analyze import analyze_onsen
from agent.workflow.ask import answer_question
from agent.workflow.cost import summarize_usage
from agent.workflow.intent import parse_intent
from core.config import settings
from services.chat.chat_service import get_history, save_message
from services.rakuten.rakuten_service import search_hotels
from services.retrieval.retrieval_service import query_onsen_structured

logger = logging.getLogger(__name__)

# ask-mode placeholder reply. Layer 2 (semantic RAG over knowledge docs) is a
# later V2.5 chunk; until then ask-mode returns this rather than a result set.
_ASK_STUB_REPLY = (
    "Onsen knowledge Q&A is coming soon — for now I can help you find or "
    "recommend onsen."
)

# Keys on the query_onsen_structured records that OnsenResult accepts. The
# records carry EXTRA keys (description, detail_url) that are NOT fields on
# OnsenResult; since OnsenResult forbids extras (Pydantic v2 default),
# OnsenResult(**record) would raise. We project onto this allow-list instead.
_ONSEN_FIELDS = ("name", "location", "spring_type", "spa_quality", "lat", "lng")

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

    # ② Retrieval — pure Python, no LLM, no fabrication (search + recommend).
    records = query_onsen_structured(intent.query, prefecture=intent.prefecture)
    onsens = _build_onsens(records)
    logger.info("run_workflow | retrieved onsens=%d", len(onsens))

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
            # search_hotels is sync (uses requests) — run off the event loop.
            raw = await asyncio.to_thread(search_hotels, lat, lng)
            hotels = [_to_hotel(h) for h in raw]
            logger.info("run_workflow | hotels=%d (lat=%s lng=%s)", len(hotels), lat, lng)
        else:
            logger.warning("run_workflow | wants_hotels but no onsen has coords — skipping hotels")

    # ④ Reply — template, no LLM.
    reply = _build_reply(intent.prefecture, onsens, hotels)

    _log_cost(session_id, intent.mode, usage_cb, started)
    save_message(session_id, message, reply)
    return AgentResponse(
        reply=reply, onsens=onsens, hotels=hotels, recommendation=recommendation
    ).model_dump()
