import asyncio
import logging

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from agent.tools.retrieval_tool import search_onsen
from agent.tools.geocoding_tool import geocode_location
from agent.tools.rakuten_tool import search_rakuten_onsen
from services.chat.chat_service import get_history, save_message
from services.geocoding.geocoding_service import geocode
from core.config import settings
from core.exceptions import GeocodingError

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an expert guide for Japanese hot springs (onsen). "
    "Help English-speaking travellers find the perfect onsen. "
    "Use the available tools to search for onsen (each onsens has spring type), geocode locations, "
    "and find nearby hotels via Rakuten Travel. "
    "When the user names a location or region (e.g. 'an onsen in Okinawa'), extract the prefecture "
    "and pass it to the search_onsen tool's `prefecture` argument as the English prefecture name "
    "(e.g. 'Okinawa', 'Mie', 'Tokyo'); this restricts results to that prefecture. If the user does "
    "not specify a location, omit the prefecture argument. "
    "List out onsens along with name, location and sale point in the reply. "
    "CRITICAL — onsen: every onsen you return MUST come verbatim from the "
    "search_onsen tool's output. If search_onsen returned no matches (e.g. it "
    "responded 'No onsen found matching your query') or you did not call it, the "
    "onsens list MUST be empty and the reply must say none were found. NEVER "
    "invent, guess, or recall onsen names, locations, spring types, spa quality, "
    "or descriptions from your own knowledge — only report onsen present in the "
    "tool output. "
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
    sales_point: str | None = Field(default=None, description="Hot spring sales point")
    lat: float | None = Field(default=None, description="Latitude of the onsen")
    lng: float | None = Field(default=None, description="Longitude of the onsen")

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
    model="gpt-4o",
    api_key=settings.openai_api_key,
)

tools = [search_onsen, geocode_location, search_rakuten_onsen]

graph = create_react_agent(llm, tools, prompt=_SYSTEM_PROMPT, response_format=AgentResponse)

async def _enrich_coordinates(onsen: OnsenResult) -> None:
    query = f"{onsen.name}, {onsen.location}, Japan" if onsen.location else f"{onsen.name}, Japan"
    try:
        coords = await asyncio.to_thread(geocode, query)
        onsen.lat = coords["latitude"]
        onsen.lng = coords["longitude"]
    except GeocodingError:
        pass


async def run_agent(message: str, session_id: str) -> dict:
    history = get_history(session_id)
    result = await graph.ainvoke({"messages": history + [HumanMessage(content=message)]})
    structured: AgentResponse = result["structured_response"]
    if structured.onsens:
        await asyncio.gather(*[_enrich_coordinates(o) for o in structured.onsens])
    save_message(session_id, message, structured.reply)
    return structured.model_dump()
