from langchain_core.tools import tool

from services.rakuten.rakuten_service import search_hotels


@tool
def search_rakuten_onsen(latitude: float, longitude: float) -> list:
    """Search Rakuten Travel for onsen hotels near the given coordinates."""
    return search_hotels(latitude, longitude)
