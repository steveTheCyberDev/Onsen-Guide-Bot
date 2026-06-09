"""Intent-parsing node for the V2 onsen workflow.

A small, fast LLM call that replaces the ReAct agent's first two GPT-4o
round-trips (tool-selection + routing). It reads the user message (plus
conversation history so follow-ups resolve) and returns a tiny typed
``Intent`` object — no facts, just routing signals for the downstream
deterministic pipeline.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from core.config import settings

logger = logging.getLogger(__name__)


class Intent(BaseModel):
    """Routing signals extracted from a user's onsen request."""

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


_INSTRUCTIONS = (
    "You parse a traveller's message about Japanese hot springs (onsen) into "
    "routing signals for a search pipeline. Extract three things:\n"
    "1. prefecture: if the user names a location, return its English prefecture "
    "name only (e.g. 'Shizuoka', 'Okinawa', 'Tokyo'). Use just the prefecture "
    "name without the word 'Prefecture'. If the user names no location, return "
    "null.\n"
    "2. query: a concise semantic search query capturing the kind of onsen the "
    "user wants (spring type, mood, scenery, features). Do not include the "
    "prefecture in the query.\n"
    "3. wants_hotels: true if the user asks about hotels, lodging, accommodation, "
    "or where to stay; otherwise false.\n"
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


async def parse_intent(message: str, history: list) -> Intent:
    """Parse a user message into routing signals for the onsen workflow.

    Args:
        message: The latest user message.
        history: Conversation history as a list of LangChain messages (same
            shape ``run_agent`` gets from ``get_history``), passed through so
            follow-up questions resolve against prior turns.

    Returns:
        An ``Intent`` with the extracted prefecture, semantic query, and
        whether the user wants hotels.
    """
    messages = [
        SystemMessage(content=_INSTRUCTIONS),
        *history,
        HumanMessage(content=message),
    ]
    run_config = {
        "run_name": "parse-intent",
        "tags": ["workflow", "intent", f"model:{settings.intent_model}"],
        "metadata": {
            "node": "parse_intent",
            "intent_model": settings.intent_model,
            "version": "v2-workflow",
        },
    }
    intent: Intent = await _llm.ainvoke(messages, config=run_config)
    logger.info(
        "parse_intent | prefecture=%s | wants_hotels=%s",
        intent.prefecture,
        intent.wants_hotels,
    )
    return intent
