import logging

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from agent.tools.retrieval_tool import search_onsen
from agent.tools.geocoding_tool import geocode_location
from agent.tools.rakuten_tool import search_rakuten_onsen
from services.chat.chat_service import get_history, save_message
from core.config import settings, export_langsmith_env

logger = logging.getLogger(__name__)

# Export LangSmith tracing env vars (if enabled) BEFORE constructing the LLM /
# graph. langsmith reads these env vars and caches them, so they must be present
# in os.environ before the first run. No-op + tracing disabled unless
# LANGSMITH_TRACING=true and an API key are set — see core.config.
_TRACING_ENABLED = export_langsmith_env()
if _TRACING_ENABLED:
    logger.info(
        "LangSmith tracing ENABLED | project=%s | endpoint=%s",
        settings.langsmith_project,
        settings.langsmith_endpoint,
    )
else:
    logger.info("LangSmith tracing disabled (no-op)")

_SYSTEM_PROMPT = (
    "You are an expert guide for Japanese hot springs (onsen). "
    "Help English-speaking travellers find the perfect onsen. "
    "Use the available tools to search for onsen (each onsens has spring type), geocode locations, "
    "and find nearby hotels via Rakuten Travel. "
    "When the user names a location or region (e.g. 'an onsen in Okinawa'), extract the prefecture "
    "and pass it to the search_onsen tool's `prefecture` argument as the English prefecture name "
    "(e.g. 'Okinawa', 'Mie', 'Tokyo'); this restricts results to that prefecture. If the user does "
    "not specify a location, omit the prefecture argument. "
    "List out onsens along with name and location in the reply. "
    "CRITICAL — onsen: every onsen you return MUST come verbatim from the "
    "search_onsen tool's output. If search_onsen returned no matches (e.g. it "
    "responded 'No onsen found matching your query') or you did not call it, the "
    "onsens list MUST be empty and the reply must say none were found. NEVER "
    "invent, guess, or recall onsen names, locations, spring types, spa quality, "
    "or descriptions from your own knowledge — only report onsen present in the "
    "tool output. "
    "CRITICAL — onsen coordinates: for each onsen, copy the `Latitude` and "
    "`Longitude` values from the search_onsen tool output VERBATIM into the "
    "OnsenResult `lat` and `lng` fields. Do NOT round them, invent them, or recall "
    "them from your own knowledge. If the tool output for an onsen has no Latitude/"
    "Longitude lines, leave `lat` and `lng` null. "
    "CRITICAL — hotels: every hotel you return MUST come verbatim from the "
    "search_rakuten_onsen tool's output. If you did not call search_rakuten_onsen, "
    "or it returned no results, the hotels list MUST be empty. NEVER invent, guess, "
    "or recall hotel names, URLs, images, prices, or coordinates from your own "
    "knowledge, and NEVER use placeholder or example URLs (e.g. anything containing "
    "'example.com'). Map each hotel field directly from the tool output: set `image` "
    "from the tool's `hotelImageUrl` field, set `url` from the tool's `url` field (the "
    "Rakuten hotelInformationUrl), and copy the tool's `lat`/`lng` verbatim. If a real "
    "field is missing from the tool output, leave it null rather than fabricating a value."
)

class OnsenResult(BaseModel):
    name: str = Field(description="Name in English")
    location: str | None = Field(default=None, description="City in English")
    spring_type: str
    spa_quality: str
    lat: float | None = Field(
        default=None,
        description=(
            "Latitude of the onsen, copied VERBATIM from the search_onsen tool "
            "output's Latitude line. Null if the tool output has no coordinates."
        ),
    )
    lng: float | None = Field(
        default=None,
        description=(
            "Longitude of the onsen, copied VERBATIM from the search_onsen tool "
            "output's Longitude line. Null if the tool output has no coordinates."
        ),
    )

class HotelResult(BaseModel):
    name: str = Field(description="Name in English")
    originalName: str = Field(description="Name in Original Language")
    location: str | None = Field(default=None, description="City and prefecture in English")
    hotelSpecial: str | None = Field(default=None, description="Display in English")
    price: str | None = Field(default=None, description="Minimum price per night in yen, numbers only e.g. '4200' — hotel results only")
    image: str | None = Field(default=None, description="Image URL — hotel results only")
    url: str | None = Field(default=None, description="Link to more information")
    lat: float | None = Field(default=None, description="Latitude of hotel")
    lng: float | None = Field(default=None, description="Longitude of hotel")


class AgentResponse(BaseModel):
    reply: str = Field(
        description=(
            "One sentence summary only. Example: 'Found 3 onsens and 10 nearby hotels in Naha, Okinawa.' "
            "No markdown, no bullet points, no listing of individual results."
        )
    )
    onsens: list[OnsenResult] = Field(
        default=[],
        description=(
            "Onsen MUST come verbatim from the search_onsen tool output ONLY. "
            "If search_onsen was not called or returned no matches, this MUST be an "
            "empty list. NEVER invent or recall onsen names, locations, spring types, "
            "spa quality, or descriptions from your own knowledge. "
            "Each onsen from the tool has name, location, spring type, spa quality and sales point."
        )
    )
    hotels: list[HotelResult] = Field(
        default=[],
        description=(
            "Hotels MUST come verbatim from the search_rakuten_onsen tool output ONLY. "
            "If search_rakuten_onsen was not called or returned no results, this MUST be an "
            "empty list. NEVER invent or recall hotel names, URLs, images, prices, or "
            "coordinates, and never use placeholder/example URLs (e.g. anything containing "
            "'example.com'). For each hotel from the tool: set `image` from the tool's "
            "`hotelImageUrl` field, set `url` from the tool's `url` field (the Rakuten "
            "hotelInformationUrl), and copy the tool's `lat` and `lng` verbatim — do not "
            "invent or round them. "
            "Translate name, hotelSpecial and location to English; keep the Japanese name in "
            "originalName. Leave any field null if the tool output does not provide it."
        )
    )


llm = ChatOpenAI(
    model=settings.chat_model,
    api_key=settings.openai_api_key,
    # Emit token-usage metadata on every response so per-step token counts show
    # up in LangSmith traces (and aggregate into the run's total). Harmless when
    # tracing is off — it just attaches usage_metadata to the AIMessage.
    stream_usage=True,
)

tools = [search_onsen, geocode_location, search_rakuten_onsen]

graph = create_react_agent(llm, tools, prompt=_SYSTEM_PROMPT, response_format=AgentResponse)

async def run_react_agent(message: str, session_id: str) -> dict:
    history = get_history(session_id)
    # Name/tag the run so this GPT-4o ReAct baseline is easy to locate and filter
    # in the LangSmith UI later (and to compare against the slot-filling/workflow
    # migration). Metadata is request-scoped; we deliberately omit the raw user
    # message to avoid logging PII into traces. Config is inert when tracing is
    # disabled.
    run_config = {
        "run_name": "chat-react-agent",
        "tags": ["chat", "react-agent", f"model:{settings.chat_model}", f"env:{settings.app_env}"],
        "metadata": {
            "endpoint": "/chat",
            "session_id": session_id,
            "chat_model": settings.chat_model,
            "agent_type": "react",
            # `version` labels the engine variant (v1-baseline vs v2-workflow);
            # `app_version` below is the deploy id (git SHA/tag) — a different axis.
            "version": "v1-baseline",
            "environment": settings.app_env,
            "app_version": settings.app_version,
        },
    }
    result = await graph.ainvoke(
        {"messages": history + [HumanMessage(content=message)]},
        config=run_config,
    )
    structured: AgentResponse = result["structured_response"]
    # Onsen coordinates come straight from the structured response, which the LLM
    # populates verbatim from the search_onsen tool output (coordinates are stored
    # in ChromaDB metadata at ingest time). No request-time geocoding is performed.
    save_message(session_id, message, structured.reply)
    return structured.model_dump()


async def run_agent(message: str, session_id: str) -> dict:
    """Dispatch /chat to the configured chat engine (the A/B + rollback seam).

    Routes on ``settings.chat_engine`` (env ``CHAT_ENGINE``):
      * ``"workflow"`` → the deterministic V2 pipeline (``run_workflow``).
      * anything else (incl. ``"react"``, the default) → the legacy ReAct agent.

    Keeps the public name/signature/return shape (dict) stable so
    ``api/routes/chat.py`` is unchanged. ``run_workflow`` is imported lazily here
    to avoid a circular import: ``agent.workflow.pipeline`` imports the Pydantic
    models (AgentResponse/HotelResult/OnsenResult) from this module at top level.

    Args:
        message: The latest user message.
        session_id: Conversation/session identifier.

    Returns:
        ``AgentResponse.model_dump()`` — same dict shape from either engine.
    """
    if settings.chat_engine == "workflow":
        logger.info("run_agent | engine=workflow | session_id=%s", session_id)
        # Lazy import: agent.workflow.pipeline imports models from this module,
        # so a top-level import here would create an import cycle.
        from agent.workflow.pipeline import run_workflow

        return await run_workflow(message, session_id)

    logger.info("run_agent | engine=react | session_id=%s", session_id)
    return await run_react_agent(message, session_id)
