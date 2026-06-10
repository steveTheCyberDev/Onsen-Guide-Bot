from core.config import settings
from core.exceptions import GeocodingError
from services.http_retry import get_with_retries

GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def geocode(place_name: str) -> dict:
    # Retries transient failures (connection/timeout + 5xx) with jittered backoff;
    # 4xx and the existing 10s per-request timeout are preserved (see http_retry).
    response = get_with_retries(
        GEOCODING_URL,
        params={"address": place_name, "key": settings.google_maps_api_key},
        timeout=10,
    )
    data = response.json()

    if data.get("status") != "OK" or not data.get("results"):
        raise GeocodingError(f"Could not geocode: {place_name}")

    location = data["results"][0]["geometry"]["location"]
    return {"latitude": location["lat"], "longitude": location["lng"]}
