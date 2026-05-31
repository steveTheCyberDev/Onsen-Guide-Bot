"""API tests for POST /hotels (api.routes.hotels).

The route currently returns a static list of 4 mock HotelItems (real Rakuten
wiring is a TODO), so no external mocking is required.
"""


def _valid_body():
    return {"latitude": 26.2124, "longitude": 127.6809, "radius": 3}


def test_hotels_returns_200(client):
    # Act
    response = client.post("/hotels", json=_valid_body())
    # Assert
    assert response.status_code == 200


def test_hotels_returns_four_mock_hotels(client):
    # Act
    hotels = client.post("/hotels", json=_valid_body()).json()["hotels"]
    # Assert
    assert len(hotels) == 4


def test_hotels_each_item_has_required_fields(client):
    # Act
    hotels = client.post("/hotels", json=_valid_body()).json()["hotels"]
    # Assert — name and originalName are non-optional on HotelItem
    assert all(h["name"] and h["originalName"] for h in hotels)


def test_hotels_first_item_is_naha_terrace(client):
    # Act
    hotels = client.post("/hotels", json=_valid_body()).json()["hotels"]
    # Assert
    assert hotels[0]["name"] == "Naha Terrace Hotel"


def test_hotels_item_exposes_original_japanese_name(client):
    # Act
    hotels = client.post("/hotels", json=_valid_body()).json()["hotels"]
    # Assert
    assert hotels[0]["originalName"] == "那覇テラスホテル"


def test_hotels_radius_defaults_when_omitted(client):
    # Arrange — radius has a default of 3, so request should still succeed
    response = client.post("/hotels", json={"latitude": 26.2, "longitude": 127.6})
    # Assert
    assert response.status_code == 200


def test_hotels_returns_422_when_latitude_missing(client):
    # Act
    response = client.post("/hotels", json={"longitude": 127.6})
    # Assert
    assert response.status_code == 422
