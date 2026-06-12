"""ASK brain for the V2.5 workflow — ``answer_question``.

Layer 2 of the knowledge base: a grounded Q&A over prose knowledge docs
(etiquette, bathing steps, spring-type benefits, tattoo guidance, trip prep).
Mirrors ``analyze.py``'s shape — a module-level ``ChatOpenAI`` built once at
import, a strict-grounding ``_INSTRUCTIONS`` constant, a ``run_config`` with
``run_name``/``tags``/``metadata``, and ``callbacks`` threaded through for usage
capture.

GROUNDING is the whole point, same as the recommend brain: the prompt feeds the
retrieved KB chunks (each rendered with its ``doc_type``/``heading`` so the model
can attribute) and instructs the model to answer ONLY from those passages — never
inventing facts and, critically, never stating per-onsen tattoo policies, prices,
or hours (high-stakes fields deliberately kept out of the KB).

Two safety nets keep "I don't know" honest rather than fabricated:
  1. a DETERMINISTIC short-circuit — if retrieval is empty/all-filtered we return
     the no-info fallback WITHOUT an LLM call (cheap, no fabrication risk), and
  2. the strict grounding prompt, which tells the model to reply with the exact
     same fallback sentence when the passages don't contain the answer.

The output is a PLAIN string (not structured) — the answer is the reply, riding
in the existing ``AgentResponse.reply`` field, so no schema change is needed.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from core.config import settings
from services.retrieval.retrieval_service import query_knowledge_with_diagnostics

logger = logging.getLogger(__name__)

# Current-run accessor, guarded exactly like agent/workflow/pipeline.py: when
# langsmith is absent (or tracing disabled) the name resolves to a no-op
# returning None, so the no-info tagging path below is inert rather than raising.
try:
    from langsmith import get_current_run_tree
except Exception:  # pragma: no cover - langsmith always present in this project

    def get_current_run_tree():
        return None


# How many chars of the question to carry into logs/trace metadata. Bounded so a
# long question can't bloat the log line or the run-tree payload; full questions
# are deliberately not logged in production (privacy + noise).
_QUESTION_LOG_CHARS = 200


def _instrument_no_info(path: str, query: str, diagnostics: dict) -> None:
    """Make an ask-mode no-info outcome queryable for the KB-coverage governor.

    Emits a structured log line AND, when a LangSmith run tree is available,
    attaches metadata + an ``ask_no_info`` tag to the live run so every no-info
    reply can later be sliced into a TRUE coverage gap (high ``min_distance`` /
    nothing retrieved) vs a FALSE refusal (low ``min_distance`` — a relevant
    chunk WAS retrieved but the grounding prompt declined).

    Args:
        path: Which no-info path fired — "empty_retrieval" (deterministic
            short-circuit, no LLM call) or "llm_refusal" (grounding prompt
            returned NO_INFO_REPLY).
        query: The user's question (truncated before it reaches logs/trace).
        diagnostics: The dict from ``query_knowledge_with_diagnostics`` —
            ``min_distance`` / ``retrieved`` / ``kept``.

    FULLY fail-safe: mirrors pipeline._attach_cost_to_trace. Any error in
    logging/tagging is swallowed so instrumentation never leaks into the request
    path. Does NOT change the reply string or the /chat contract.
    """
    min_distance = diagnostics.get("min_distance")
    retrieved = diagnostics.get("retrieved")
    kept = diagnostics.get("kept")
    question = query[:_QUESTION_LOG_CHARS]

    # Structured log line first — this must fire even if the run-tree tagging
    # below is unavailable, so the no-info outcome is queryable in logs alone.
    logger.info(
        "ask_no_info | path=%s | min_distance=%s | retrieved=%s | kept=%s | question=%r",
        path,
        min_distance,
        retrieved,
        kept,
        question,
    )

    # Best-effort: attach to the active LangSmith run so no-info outcomes are
    # sliceable in the trace UI. Inert when tracing is off / no active run.
    try:
        rt = get_current_run_tree()
        if rt is None:
            return
        rt.metadata.update(
            {
                "ask_no_info": True,
                "ask_no_info_path": path,
                "ask_min_distance": min_distance,
                "ask_retrieved": retrieved,
                "ask_kept": kept,
                "ask_question": question,
            }
        )
        rt.tags = (rt.tags or []) + ["ask_no_info", f"ask_no_info_path:{path}"]
    except Exception:  # pragma: no cover - defensive; trace must never break /chat
        logger.debug("failed to attach ask no-info metadata to langsmith run", exc_info=True)

# The single canonical "I don't have that" reply. SHARED by the deterministic
# no-retrieval short-circuit below AND quoted verbatim into the grounding prompt
# so the LLM falls back to the EXACT same sentence when the passages don't answer
# the question — the two paths must never drift. Lists the KB's actual coverage
# so the user knows what to ask instead.
NO_INFO_REPLY = (
    "I don't have that information yet — I can help with onsen etiquette, "
    "bathing steps, spring-type benefits, tattoo guidance, and trip prep."
)

_INSTRUCTIONS = (
    "You are an expert guide for Japanese hot springs (onsen). Answer the "
    "traveller's question using ONLY the knowledge passages provided below.\n"
    "STRICT GROUNDING RULES:\n"
    "- Answer ONLY from the provided knowledge passages. Do NOT use outside "
    "knowledge or general assumptions.\n"
    "- If the passages do not contain the answer, reply EXACTLY with this "
    f"sentence and nothing else: {NO_INFO_REPLY}\n"
    "- Do NOT state per-onsen tattoo policies, prices, or opening hours — those "
    "are not in this knowledge base and vary by facility. Speak only in the "
    "general terms the passages support.\n"
    "- Be concise and practical. You may attribute guidance to the topic it came "
    "from (e.g. etiquette, bathing) when helpful."
)

# Construct the ask model once at import time. Reuses intent_model by DEFAULT
# (cheap; the answer is short and fully grounded over <=4 short chunks), with the
# dedicated ask_model knob as an env override to a stronger model. Plain string
# output (no .with_structured_output) — the answer IS the reply.
_llm = ChatOpenAI(
    model=settings.ask_model or settings.intent_model,
    api_key=settings.openai_api_key,
    stream_usage=True,
)


def _render_chunks(records: list[dict]) -> str:
    """Render retrieved KB chunks for the prompt, attributed by doc_type/heading.

    Each chunk is labelled with its ``doc_type`` and ``heading`` so the model can
    attribute its answer; the per-section ``**Source:**`` citation lines already
    live inside the chunk text, so provenance travels with the passage.
    """
    blocks: list[str] = []
    for i, r in enumerate(records):
        doc_type = r.get("doc_type") or "knowledge"
        heading = r.get("heading") or ""
        label = f"[{i}] ({doc_type}"
        label += f" — {heading})" if heading else ")"
        blocks.append(f"{label}\n{r.get('text', '')}")
    return "\n\n---\n\n".join(blocks)


async def answer_question(query: str, callbacks: list | None = None) -> str:
    """Grounded ask-mode answer over the Layer 2 KB. Returns a reply string.

    Args:
        query: The user's question (the ``Intent.query`` from the intent node),
            reused directly as the KB retrieval query.
        callbacks: Optional LangChain callbacks (e.g. a
            ``UsageMetadataCallbackHandler``) for token-usage capture, threaded
            into the LLM ``run_config`` exactly as ``analyze_onsen`` does.

    Returns:
        The grounded answer string, or ``NO_INFO_REPLY`` when retrieval surfaces
        nothing usable (deterministic, no LLM call) or the model determines the
        passages don't answer the question.
    """
    records, diagnostics = query_knowledge_with_diagnostics(
        query, settings.ask_top_k, settings.ask_max_distance
    )
    if not records:
        # Deterministic no-info path: no chunk survived retrieval/threshold, so
        # there is nothing to ground on. Return the canonical fallback WITHOUT an
        # LLM call — cheap and structurally incapable of fabricating. Instrument
        # this no-info outcome (empty_retrieval) before returning.
        _instrument_no_info("empty_retrieval", query, diagnostics)
        return NO_INFO_REPLY

    # TODO(spring-type injection): when the question is reliably detectable as
    # spring-type-specific, append benefits_for(spring_type) from
    # agent.workflow.spring_benefits. Detection is non-trivial (no structured
    # spring_type on an ask query) and out of scope for this PR; the KB's
    # spring_types.md chunks already cover spring-type questions via retrieval.

    messages = [
        SystemMessage(content=_INSTRUCTIONS),
        HumanMessage(
            content=(
                f"Traveller's question: {query}\n\n"
                f"Knowledge passages:\n{_render_chunks(records)}"
            )
        ),
    ]
    run_config: dict = {
        "run_name": "answer-question",
        "tags": ["workflow", "ask", f"model:{settings.ask_model or settings.intent_model}"],
        "metadata": {
            "node": "answer_question",
            "ask_model": settings.ask_model or settings.intent_model,
            "version": "v2-workflow",
        },
    }
    if callbacks:
        run_config["callbacks"] = callbacks

    result = await _llm.ainvoke(messages, config=run_config)
    content = result.content if hasattr(result, "content") else result
    # Coerce to str (content can be non-string for some model outputs) and strip:
    # the no-info fallback must match NO_INFO_REPLY exactly, and a stray
    # trailing newline would otherwise flap the eval's exact-equality check.
    answer = (content if isinstance(content, str) else str(content)).strip()
    if answer == NO_INFO_REPLY:
        # LLM-refusal no-info path: chunks WERE retrieved (low min_distance) but
        # the grounding prompt judged they don't answer the question and replied
        # with the canonical fallback verbatim. Instrument so this FALSE refusal
        # can be told apart from a TRUE coverage gap via the captured diagnostics.
        _instrument_no_info("llm_refusal", query, diagnostics)
        return answer
    logger.info("answer_question | answered from chunks=%d", len(records))
    return answer
