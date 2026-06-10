import logging

from core.config import settings
from core.exceptions import RakutenError
from services.http_retry import get_with_retries

logger = logging.getLogger(__name__)

# Number of hotel results to return per page.
# Rakuten SimpleHotelSearch `hits` accepts an integer 1–30 (default 30); we request 10.
RAKUTEN_HITS_PER_PAGE = 10

# Response format for the Rakuten API. `format` accepts "json" or "xml" (default "json").
RAKUTEN_RESPONSE_FORMAT = "json"

# Coordinate system for input/output lat-lng values (`datumType`).
# 1 = WGS84 (GPS), unit is degrees; 2 = Tokyo Datum, unit is seconds (API default).
# We send WGS84 because our coordinates come from Google Geocoding (degrees).
RAKUTEN_DATUM_TYPE_WGS84 = 1

def search_hotels(latitude: float, longitude: float, radius: int = 3) -> list:
    url = settings.rakuten_hotel_url
    params = {
        "applicationId": settings.rakuten_app_id,
        "latitude": latitude,
        "longitude": longitude,
        "searchRadius": radius,
        "hits": RAKUTEN_HITS_PER_PAGE,
        "format": RAKUTEN_RESPONSE_FORMAT,
        "datumType": RAKUTEN_DATUM_TYPE_WGS84,
    }
    headers = {
        "accessKey": settings.rakuten_access_key,
    }

    logger.info(
        "Rakuten request | url=%s | params=%s",
        url,
        {k: v for k, v in params.items() if k not in ("applicationId", "accessKey")},
    )

    # Retries transient failures (connection/timeout + 5xx) with jittered backoff;
    # 4xx and the existing 10s per-request timeout are preserved (see http_retry).
    response = get_with_retries(url, headers=headers, params=params, timeout=10)
    logger.info("Rakuten actual request URL | %s", response.request.url)

    data = response.json()

    logger.info("Rakuten response | status=%s | body=%s", response.status_code, data)

    if "error" in data:
        raise RakutenError(data.get("error_description", "Rakuten API error"))

    hotels = data.get("hotels", [])
    if not hotels:
        # No error key but zero hotels — often a silent config problem rather than a
        # genuinely empty area (e.g. this server's egress IP is not in Rakuten's
        # Allowed IP list, or bad credentials). Surface it loudly with the raw body.
        logger.warning(
            "Rakuten returned 0 hotels with no error field — verify the Allowed IP "
            "list (this server's outbound IP must be allowlisted) and credentials. "
            "lat=%s lng=%s radius=%s | raw_response=%s",
            latitude, longitude, radius, data,
        )
    hotel_list = [
        {
            "name": h["hotel"][0]["hotelBasicInfo"]["hotelName"],
            "address": h["hotel"][0]["hotelBasicInfo"]["address1"],
            "price": h["hotel"][0]["hotelBasicInfo"].get("hotelMinCharge"),
            "hotelSpecial": h["hotel"][0]["hotelBasicInfo"]["hotelSpecial"],
            "access": h["hotel"][0]["hotelBasicInfo"]["access"],
            "lat": h["hotel"][0]["hotelBasicInfo"]["latitude"],
            "lng": h["hotel"][0]["hotelBasicInfo"]["longitude"],
            "parkingInformation": h["hotel"][0]["hotelBasicInfo"]["parkingInformation"],
            "nearestStation": h["hotel"][0]["hotelBasicInfo"]["nearestStation"],
            "hotelImageUrl": h["hotel"][0]["hotelBasicInfo"]["hotelImageUrl"],
            "url": h["hotel"][0]["hotelBasicInfo"]["hotelInformationUrl"],
        }
        for h in hotels
    ]

    logger.info("Final response | hotels=%s", hotel_list)

    return hotel_list
