import logging

from fastapi import APIRouter
from pydantic import BaseModel

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


class HotelsResponse(BaseModel):
    hotels: list[HotelItem]


_MOCK_HOTELS = [
    HotelItem(
        name="Naha Terrace Hotel",
        originalName="那覇テラスホテル",
        location="Naha, Okinawa",
        hotelSpecial="Rooftop onsen with ocean views and traditional Ryukyuan cuisine",
        price="12000",
        image="https://placehold.co/400x240?text=Naha+Terrace",
        url="https://example.com/naha-terrace",
    ),
    HotelItem(
        name="Okinawa Hot Spring Resort",
        originalName="沖縄温泉リゾート",
        location="Naha, Okinawa",
        hotelSpecial="Natural hot spring baths with sea salt mineral water, beachfront location",
        price="18500",
        image="https://placehold.co/400x240?text=Okinawa+Resort",
        url="https://example.com/okinawa-resort",
    ),
    HotelItem(
        name="Ryukyu Garden Inn",
        originalName="琉球ガーデンイン",
        location="Naha, Okinawa",
        hotelSpecial="Intimate boutique inn with private outdoor baths and garden views",
        price="9800",
        image="https://placehold.co/400x240?text=Ryukyu+Garden",
        url="https://example.com/ryukyu-garden",
    ),
    HotelItem(
        name="Shuri Castle Hotel",
        originalName="首里城ホテル",
        location="Shuri, Naha, Okinawa",
        hotelSpecial="Historic property near Shuri Castle, communal baths with local herbs",
        price="15000",
        image="https://placehold.co/400x240?text=Shuri+Castle+Hotel",
        url="https://example.com/shuri-hotel",
    ),
]


@router.post("", response_model=HotelsResponse)
async def get_hotels(request: HotelsRequest):
    logger.info(
        "POST /hotels request | lat=%.4f | lng=%.4f | radius=%d",
        request.latitude,
        request.longitude,
        request.radius,
    )
    # TODO: replace with real Rakuten service call
    return HotelsResponse(hotels=_MOCK_HOTELS)
