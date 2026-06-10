import asyncio
import logging
from math import asin, cos, radians, sin, sqrt

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.limiter import limiter
from core.config import settings
from core.exceptions import OnsenBotError
from services.rakuten.rakuten_service import search_hotels

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hotels", tags=["hotels"])


class HotelsRequest(BaseModel):
    latitude: float
    longitude: float
    radius: int = 3


class HotelItem(BaseModel):
    name: str
    originalName: str
    location: str | None = None
    hotelSpecial: str | None = None
    price: str | None = None
    image: str | None = None
    url: str | None = None
    lat: float | None = None
    lng: float | None = None
    distance: float | None = None  # km from the query point


class HotelsResponse(BaseModel):
    hotels: list[HotelItem]


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two WGS84 points."""
    r = 6371
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return round(2 * r * asin(sqrt(a)), 2)


def _to_item(h: dict, origin_lat: float, origin_lng: float) -> HotelItem:
    """Map a Rakuten service hotel dict to the frontend HotelItem shape.

    Rakuten returns Japanese-only names (V1: shown as-is, no translation), so
    name and originalName are the same Japanese string.
    """
    name = h.get("name") or ""
    lat = h.get("lat")
    lng = h.get("lng")
    price = h.get("price")
    distance = (
        _haversine_km(origin_lat, origin_lng, lat, lng)
        if lat is not None and lng is not None
        else None
    )
    return HotelItem(
        name=name,
        originalName=name,
        location=h.get("address"),
        hotelSpecial=h.get("hotelSpecial"),
        price=str(price) if price is not None else None,
        image=h.get("hotelImageUrl"),
        url=h.get("url"),
        lat=lat,
        lng=lng,
        distance=distance,
    )


# Rate-limited per client IP (paid endpoint). The limit string comes from
# settings.rate_limit_hotels (env RATE_LIMIT_HOTELS). slowapi requires the
# `request: Request` parameter to read the client key; the parsed body stays in
# `payload`.
@router.post("", response_model=HotelsResponse)
@limiter.limit(settings.rate_limit_hotels)
async def get_hotels(request: Request, payload: HotelsRequest):
    logger.info(
        "POST /hotels request | lat=%.4f | lng=%.4f | radius=%d",
        payload.latitude,
        payload.longitude,
        payload.radius,
    )
    try:
        # search_hotels is sync (uses requests) — run off the event loop.
        raw = await asyncio.to_thread(
            search_hotels, payload.latitude, payload.longitude, payload.radius
        )
    except OnsenBotError as e:
        logger.error("POST /hotels service error | %s", e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        logger.exception("POST /hotels unexpected error")
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")

    hotels = [_to_item(h, payload.latitude, payload.longitude) for h in raw]
    logger.info("POST /hotels response | hotels=%d", len(hotels))
    return HotelsResponse(hotels=hotels)
