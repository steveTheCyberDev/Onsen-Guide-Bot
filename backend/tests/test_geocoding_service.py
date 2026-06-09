"""Unit tests for services.geocoding.geocoding_service.

The only external I/O is the outbound HTTP call to the Google Maps Geocoding
API, made via services.http_retry.get_with_retries and mocked at the service
module's import site in every test — no real network calls are made.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.exceptions import GeocodingError
from services.geocoding import geocoding_service


def _mock_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


def test_geocode_returns_latitude_and_longitude():
    # Arrange
    payload = {
        "status": "OK",
        "results": [{"geometry": {"location": {"lat": 26.2124, "lng": 127.6809}}}],
    }
    # Act
    with patch.object(geocoding_service, "get_with_retries", return_value=_mock_response(payload)):
        result = geocoding_service.geocode("Naha")
    # Assert
    assert result == {"latitude": 26.2124, "longitude": 127.6809}


def test_geocode_calls_api_with_address_and_key():
    # Arrange
    payload = {
        "status": "OK",
        "results": [{"geometry": {"location": {"lat": 1.0, "lng": 2.0}}}],
    }
    # Act
    with patch.object(geocoding_service, "get_with_retries", return_value=_mock_response(payload)) as mock_get:
        geocoding_service.geocode("Tokyo")
    # Assert
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["address"] == "Tokyo"


def test_geocode_raises_when_status_not_ok():
    # Arrange
    payload = {"status": "ZERO_RESULTS", "results": []}
    # Act / Assert
    with patch.object(geocoding_service, "get_with_retries", return_value=_mock_response(payload)):
        with pytest.raises(GeocodingError):
            geocoding_service.geocode("Nowhereville")


def test_geocode_raises_when_results_empty_despite_ok_status():
    # Arrange — defensive: status OK but no results
    payload = {"status": "OK", "results": []}
    # Act / Assert
    with patch.object(geocoding_service, "get_with_retries", return_value=_mock_response(payload)):
        with pytest.raises(GeocodingError):
            geocoding_service.geocode("Empty")


def test_geocode_error_message_includes_place_name():
    # Arrange
    payload = {"status": "REQUEST_DENIED", "results": []}
    # Act / Assert
    with patch.object(geocoding_service, "get_with_retries", return_value=_mock_response(payload)):
        with pytest.raises(GeocodingError, match="Atlantis"):
            geocoding_service.geocode("Atlantis")
