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
    "and find nearby hotels via Rakuten Travel."
    "List out onsens along with name, location and sale point in the reply"
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
            "Each Onsen in the search result has name, location, sprint type, spa quality and sales point"
        )
    )
    hotels: list[HotelResult] = Field(
        default=[],
        description=(
            "Hotel results from the Rakuten Travel tool only. "
            "Populate name, originalName, location, hotelSpecial, price, image, url. "
            "Copy lat and lng verbatim from the tool output for each hotel — do not invent or round them. "
            "Translate hotel names, hotelSpecial and locations to English; keep the Japanese name in originalName."
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
