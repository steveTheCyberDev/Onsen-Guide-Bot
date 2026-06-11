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
from services.retrieval.retrieval_service import query_knowledge

logger = logging.getLogger(__name__)

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
    records = query_knowledge(query, settings.ask_top_k, settings.ask_max_distance)
    if not records:
        # Deterministic no-info path: no chunk survived retrieval/threshold, so
        # there is nothing to ground on. Return the canonical fallback WITHOUT an
        # LLM call — cheap and structurally incapable of fabricating.
        logger.info("answer_question | no KB chunks retrieved — returning no-info reply")
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
    answer = result.content if hasattr(result, "content") else str(result)
    logger.info("answer_question | answered from chunks=%d", len(records))
    return answer
