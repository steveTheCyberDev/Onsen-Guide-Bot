"""Unit tests for services.rakuten.rakuten_service.

`requests.get` to the Rakuten Travel API is mocked in every test. The service
parses the nested `hotels[].hotel[0].hotelBasicInfo` structure into a flat dict.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.exceptions import RakutenError
from services.rakuten import rakuten_service


def _basic_info(**overrides):
    info = {
        "hotelName": "Naha Onsen Hotel",
        "address1": "Okinawa, Naha",
        "hotelMinCharge": 12000,
        "hotelSpecial": "Rooftop onsen with ocean view",
        "access": "10 min from Naha Airport",
        "latitude": 26.2124,
        "longitude": 127.6809,
        "parkingInformation": "Free parking",
        "nearestStation": "Asahibashi",
        "hotelImageUrl": "https://img.example.com/naha.jpg",
        "hotelInformationUrl": "https://example.com/naha",
    }
    info.update(overrides)
    return info


def _hotel_entry(**overrides):
    return {"hotel": [{"hotelBasicInfo": _basic_info(**overrides)}]}


def _mock_response(payload, status_code=200):
    response = MagicMock()
    response.json.return_value = payload
    response.status_code = status_code
    response.request.url = "https://example.com/rakuten?stub"
    return response


def test_search_hotels_returns_parsed_list():
    # Arrange
    payload = {"hotels": [_hotel_entry(), _hotel_entry(hotelName="Second Hotel")]}
    # Act
    with patch.object(rakuten_service.requests, "get", return_value=_mock_response(payload)):
        result = rakuten_service.search_hotels(26.2, 127.6)
    # Assert
    assert len(result) == 2


def test_search_hotels_maps_hotel_name():
    # Arrange
    payload = {"hotels": [_hotel_entry(hotelName="Ryukyu Inn")]}
    # Act
    with patch.object(rakuten_service.requests, "get", return_value=_mock_response(payload)):
        result = rakuten_service.search_hotels(26.2, 127.6)
    # Assert
    assert result[0]["name"] == "Ryukyu Inn"


def test_search_hotels_maps_coordinates():
    # Arrange
    payload = {"hotels": [_hotel_entry(latitude=34.05, longitude=135.0)]}
    # Act
    with patch.object(rakuten_service.requests, "get", return_value=_mock_response(payload)):
        result = rakuten_service.search_hotels(26.2, 127.6)
    # Assert
    assert result[0]["lat"] == 34.05 and result[0]["lng"] == 135.0


def test_search_hotels_maps_url_from_hotel_information_url():
    # Arrange
    payload = {"hotels": [_hotel_entry(hotelInformationUrl="https://example.com/x")]}
    # Act
    with patch.object(rakuten_service.requests, "get", return_value=_mock_response(payload)):
        result = rakuten_service.search_hotels(26.2, 127.6)
    # Assert
    assert result[0]["url"] == "https://example.com/x"


def test_search_hotels_price_defaults_to_none_when_missing():
    # Arrange — hotelMinCharge absent; service uses .get() so it should be None
    info = _basic_info()
    del info["hotelMinCharge"]
    payload = {"hotels": [{"hotel": [{"hotelBasicInfo": info}]}]}
    # Act
    with patch.object(rakuten_service.requests, "get", return_value=_mock_response(payload)):
        result = rakuten_service.search_hotels(26.2, 127.6)
    # Assert
    assert result[0]["price"] is None


def test_search_hotels_empty_when_no_hotels_key():
    # Arrange
    payload = {"hotels": []}
    # Act
    with patch.object(rakuten_service.requests, "get", return_value=_mock_response(payload)):
        result = rakuten_service.search_hotels(26.2, 127.6)
    # Assert
    assert result == []


def test_search_hotels_raises_rakuten_error_on_error_field():
    # Arrange
    payload = {"error": "wrong_parameter", "error_description": "Invalid lat/lng"}
    # Act / Assert
    with patch.object(rakuten_service.requests, "get", return_value=_mock_response(payload)):
        with pytest.raises(RakutenError, match="Invalid lat/lng"):
            rakuten_service.search_hotels(0.0, 0.0)


def test_search_hotels_raises_with_default_message_when_no_description():
    # Arrange — error present but no error_description
    payload = {"error": "wrong_parameter"}
    # Act / Assert
    with patch.object(rakuten_service.requests, "get", return_value=_mock_response(payload)):
        with pytest.raises(RakutenError, match="Rakuten API error"):
            rakuten_service.search_hotels(0.0, 0.0)


def test_search_hotels_warns_when_empty_with_no_error(caplog):
    # Arrange — valid response, no error, but zero hotels (e.g. egress IP not
    # allowlisted). The service should still return [] but log a WARNING so the
    # misconfig is visible instead of silently looking like "no hotels nearby".
    payload = {"hotels": []}
    # Act
    with patch.object(rakuten_service.requests, "get", return_value=_mock_response(payload)):
        with caplog.at_level("WARNING"):
            result = rakuten_service.search_hotels(26.2, 127.6)
    # Assert
    assert result == []
    assert "0 hotels" in caplog.text
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_search_hotels_no_warning_when_results_present(caplog):
    # Arrange
    payload = {"hotels": [_hotel_entry()]}
    # Act
    with patch.object(rakuten_service.requests, "get", return_value=_mock_response(payload)):
        with caplog.at_level("WARNING"):
            rakuten_service.search_hotels(26.2, 127.6)
    # Assert — no empty-result warning when hotels came back
    assert "0 hotels" not in caplog.text


def test_search_hotels_passes_radius_param():
    # Arrange
    payload = {"hotels": []}
    # Act
    with patch.object(rakuten_service.requests, "get", return_value=_mock_response(payload)) as mock_get:
        rakuten_service.search_hotels(26.2, 127.6, radius=5)
    # Assert
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["searchRadius"] == 5
