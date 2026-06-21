"""Intent-parsing node for the V2 onsen workflow.

A small, fast LLM call that replaces the ReAct agent's first two GPT-4o
round-trips (tool-selection + routing). It reads the user message (plus
conversation history so follow-ups resolve) and returns a tiny typed
``Intent`` object — no facts, just routing signals for the downstream
deterministic pipeline.
"""

import logging
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from core.config import settings

logger = logging.getLogger(__name__)


class Intent(BaseModel):
    """Routing signals extracted from a user's onsen request."""

    mode: Literal["search", "recommend", "ask"] = Field(
        default="search",
        description=(
            "How to handle the request. 'search': location- or fact-based listing "
            "('find onsen in Okinawa'). 'recommend': preference/vibe-based, wants a "
            "judged pick ('a relaxing onsen with mountain views', 'good for families'). "
            "'ask': general onsen knowledge Q&A not tied to a result set ('do they "
            "allow tattoos?', 'what should I bring?', etiquette)."
        ),
    )
    prefecture: str | None = Field(
        default=None,
        description=(
            "English prefecture name (e.g. 'Shizuoka', 'Okinawa') or None if the "
            "user named no location"
        ),
    )
    query: str = Field(
        description="A concise semantic search query for the onsen vector store"
    )
    wants_hotels: bool = Field(
        default=False,
        description="True if the user asks about hotels / lodging / where to stay",
    )
    limit: int | None = Field(
        default=None,
        description=(
            "The number of onsen the user explicitly asked for (e.g. 'top 5', "
            "'show me 3') as a positive integer; null if the user named no count"
        ),
    )


_INSTRUCTIONS = (
    "You parse a traveller's message about Japanese hot springs (onsen) into "
    "routing signals for a search pipeline. Extract four things:\n"
    "0. mode: classify the request as one of:\n"
    "   - 'search': the user wants a location- or fact-based listing of onsen "
    "(e.g. 'find onsen in Okinawa', 'onsen near Hakone'). A raw list is fine.\n"
    "   - 'recommend': the user expresses a preference, vibe, or use-case and "
    "wants a judged pick rather than a raw list (e.g. 'a relaxing onsen with "
    "mountain views', 'somewhere good for families', 'best outdoor baths in "
    "Gunma'). Words like best/relaxing/recommend/good for/perfect signal this.\n"
    "   - 'ask': the user asks a general onsen knowledge question not tied to a "
    "specific result set (e.g. 'do onsen allow tattoos?', 'what should I bring?', "
    "etiquette/rules/customs). \n"
    "1. prefecture: if the user names a location, return its English prefecture "
    "name only (e.g. 'Shizuoka', 'Okinawa', 'Tokyo'). Use just the prefecture "
    "name without the word 'Prefecture'. If the user names no location, return "
    "null.\n"
    "2. query: a concise semantic search query capturing the kind of onsen the "
    "user wants (spring type, mood, scenery, features). Do not include the "
    "prefecture in the query.\n"
    "3. wants_hotels: true if the user asks about hotels, lodging, accommodation, "
    "or where to stay; otherwise false.\n"
    "4. limit: if the user asks for a specific number of onsen (e.g. 'top 5', "
    "'show me 3', 'list 10'), return that number as a positive integer; if the "
    "user names no count, return null.\n"
    "Use the conversation history to resolve follow-up references."
)

# Construct the routing model once at import time. Uses the cheap intent_model
# knob (default gpt-4o-mini) — NOT the main chat_model — so this routing hop
# stays fast and inexpensive. Structured output binds the Intent schema so the
# call returns a validated Intent instance directly.
_llm = ChatOpenAI(
    model=settings.intent_model,
    api_key=settings.openai_api_key,
    stream_usage=True,
    # Bounded retries on transient OpenAI errors (timeouts, 429/5xx); the OpenAI
    # SDK handles the backoff. Same knob as the main chat llm in agent/agent.py.
    max_retries=settings.llm_max_retries,
).with_structured_output(Intent)


async def parse_intent(
    message: str, history: list, callbacks: list | None = None
) -> Intent:
    """Parse a user message into routing signals for the onsen workflow.

    Args:
        message: The latest user message.
        history: Conversation history as a list of LangChain messages (same
            shape ``run_agent`` gets from ``get_history``), passed through so
            follow-up questions resolve against prior turns.
        callbacks: Optional LangChain callbacks (e.g. a
            ``UsageMetadataCallbackHandler``) so the caller can capture token
            usage — this call uses ``.with_structured_output``, so usage is not
            on the return value.

    Returns:
        An ``Intent`` with the mode, extracted prefecture, semantic query, and
        whether the user wants hotels.
    """
    messages = [
        SystemMessage(content=_INSTRUCTIONS),
        *history,
        HumanMessage(content=message),
    ]
    run_config: dict = {
        "run_name": "parse-intent",
        "tags": ["workflow", "intent", f"model:{settings.intent_model}"],
        "metadata": {
            "node": "parse_intent",
            "intent_model": settings.intent_model,
            "version": "v2-workflow",
        },
    }
    if callbacks:
        run_config["callbacks"] = callbacks
    intent: Intent = await _llm.ainvoke(messages, config=run_config)
    logger.info(
        "parse_intent | mode=%s | prefecture=%s | wants_hotels=%s | limit=%s",
        intent.mode,
        intent.prefecture,
        intent.wants_hotels,
        intent.limit,
    )
    return intent
