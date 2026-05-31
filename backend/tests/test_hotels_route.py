"""API tests for POST /hotels (api.routes.hotels).

The route calls rakuten_service.search_hotels (imported by name into the route
module) and maps each result to the frontend HotelItem shape. search_hotels is
patched at its point of use so no real network calls happen.
"""

from unittest.mock import Mock, patch

from core.exceptions import RakutenError


def _valid_body():
    return {"latitude": 26.2124, "longitude": 127.6809, "radius": 3}


def _rakuten_hotel(lat=26.2124, lng=127.6809):
    """A hotel dict shaped like rakuten_service.search_hotels output."""
    return {
        "name": "ホテル日航那覇グランドメール",
        "address": "沖縄県那覇市",
        "price": 8000,
        "hotelSpecial": "天然温泉",
        "access": "ゆいレール県庁前駅から徒歩5分",
        "lat": lat,
        "lng": lng,
        "parkingInformation": "有り",
        "nearestStation": "県庁前",
        "hotelImageUrl": "https://img.example.com/hotel.jpg",
        "url": "https://travel.rakuten.co.jp/HOTEL/123/",
    }


def test_hotels_returns_200(client):
    with patch("api.routes.hotels.search_hotels", new=Mock(return_value=[_rakuten_hotel()])):
        response = client.post("/hotels", json=_valid_body())
    assert response.status_code == 200


def test_hotels_maps_rakuten_fields_to_item(client):
    with patch("api.routes.hotels.search_hotels", new=Mock(return_value=[_rakuten_hotel()])):
        hotel = client.post("/hotels", json=_valid_body()).json()["hotels"][0]
    # Japanese name kept as-is in both fields (no translation in this endpoint)
    assert hotel["name"] == "ホテル日航那覇グランドメール"
    assert hotel["originalName"] == "ホテル日航那覇グランドメール"
    assert hotel["location"] == "沖縄県那覇市"
    assert hotel["image"] == "https://img.example.com/hotel.jpg"
    assert hotel["price"] == "8000"  # stringified
    assert hotel["lat"] == 26.2124 and hotel["lng"] == 127.6809


def test_hotels_computes_distance(client):
    # Hotel sitting exactly on the query point → distance 0.0 km
    with patch("api.routes.hotels.search_hotels", new=Mock(return_value=[_rakuten_hotel()])):
        hotel = client.post("/hotels", json=_valid_body()).json()["hotels"][0]
    assert hotel["distance"] == 0.0


def test_hotels_distance_none_when_coords_missing(client):
    hotel_no_coords = {**_rakuten_hotel(), "lat": None, "lng": None}
    with patch("api.routes.hotels.search_hotels", new=Mock(return_value=[hotel_no_coords])):
        hotel = client.post("/hotels", json=_valid_body()).json()["hotels"][0]
    assert hotel["distance"] is None


def test_hotels_returns_empty_list_when_no_results(client):
    with patch("api.routes.hotels.search_hotels", new=Mock(return_value=[])):
        hotels = client.post("/hotels", json=_valid_body()).json()["hotels"]
    assert hotels == []


def test_hotels_returns_502_on_rakuten_error(client):
    failing = Mock(side_effect=RakutenError("Rakuten is down"))
    with patch("api.routes.hotels.search_hotels", new=failing):
        response = client.post("/hotels", json=_valid_body())
    assert response.status_code == 502
    assert response.json()["detail"] == "Rakuten is down"


def test_hotels_passes_radius_to_service(client):
    mock = Mock(return_value=[])
    with patch("api.routes.hotels.search_hotels", new=mock):
        client.post("/hotels", json={"latitude": 26.2, "longitude": 127.6})
    # radius defaults to 3; service called positionally (lat, lng, radius)
    assert mock.call_args.args == (26.2, 127.6, 3)


def test_hotels_returns_422_when_latitude_missing(client):
    response = client.post("/hotels", json={"longitude": 127.6})
    assert response.status_code == 422
