import requests

from core.config import settings
from core.exceptions import GeocodingError

GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def geocode(place_name: str) -> dict:
    response = requests.get(
        GEOCODING_URL,
        params={"address": place_name, "key": settings.google_maps_api_key},
        timeout=10,
    )
    data = response.json()

    if data.get("status") != "OK" or not data.get("results"):
        raise GeocodingError(f"Could not geocode: {place_name}")

    location = data["results"][0]["geometry"]["location"]
    return {"latitude": location["lat"], "longitude": location["lng"]}
