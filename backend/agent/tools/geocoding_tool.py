from langchain_core.tools import tool

from services.geocoding.geocoding_service import geocode


@tool
def geocode_location(place_name: str) -> dict:
    """Convert a place name to latitude and longitude coordinates."""
    return geocode(place_name)
