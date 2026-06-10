"""Unit tests for outbound HTTP resilience (services.http_retry.get_with_retries).

`requests.get` is mocked at its point of use (services.http_retry.requests.get),
and tenacity's backoff sleep is neutralised so the suite stays fast — we assert
retry *behaviour* (attempt counts, what is/isn't retried, the final return), not
wall-clock backoff timing.
"""

from unittest.mock import Mock, patch

import pytest
import requests

import services.http_retry as http_retry
from services.http_retry import get_with_retries


@pytest.fixture(autouse=True)
def _no_backoff_sleep():
    """Neutralise tenacity's between-attempt sleep so retries are instant."""
    with patch("tenacity.nap.time.sleep", return_value=None):
        yield


def _response(status_code):
    resp = Mock(spec=requests.Response)
    resp.status_code = status_code
    return resp


# --- success / no-retry paths ---------------------------------------------


def test_returns_200_on_first_attempt():
    # Arrange
    ok = _response(200)
    with patch.object(http_retry.requests, "get", return_value=ok) as mock_get:
        # Act
        result = get_with_retries("http://x")
    # Assert
    assert result is ok
    assert mock_get.call_count == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429])
def test_does_not_retry_on_4xx(status):
    # Arrange — client errors won't fix themselves; return immediately
    resp = _response(status)
    with patch.object(http_retry.requests, "get", return_value=resp) as mock_get:
        # Act
        result = get_with_retries("http://x")
    # Assert
    assert result is resp
    assert mock_get.call_count == 1


def test_forwards_kwargs_to_requests_get():
    # Arrange
    ok = _response(200)
    with patch.object(http_retry.requests, "get", return_value=ok) as mock_get:
        # Act
        get_with_retries("http://x", params={"q": 1}, timeout=5)
    # Assert — caller's kwargs pass straight through
    mock_get.assert_called_once_with("http://x", params={"q": 1}, timeout=5)


# --- 5xx retry path --------------------------------------------------------


def test_retries_on_5xx_up_to_three_attempts():
    # Arrange — every attempt returns 500
    resp = _response(500)
    with patch.object(http_retry.requests, "get", return_value=resp) as mock_get:
        # Act
        result = get_with_retries("http://x")
    # Assert — 3 total attempts, then the last 5xx response is returned (no crash)
    assert mock_get.call_count == 3
    assert result is resp
    assert result.status_code == 500


def test_returns_last_5xx_response_after_exhausting_retries():
    # Arrange — distinct 5xx responses so we can prove the LAST one comes back
    first, second, third = _response(503), _response(502), _response(500)
    with patch.object(http_retry.requests, "get", side_effect=[first, second, third]) as mock_get:
        # Act
        result = get_with_retries("http://x")
    # Assert
    assert mock_get.call_count == 3
    assert result is third


def test_succeeds_after_transient_5xx():
    # Arrange — 5xx then a 200 within the attempt budget
    with patch.object(
        http_retry.requests, "get", side_effect=[_response(500), _response(200)]
    ) as mock_get:
        # Act
        result = get_with_retries("http://x")
    # Assert
    assert mock_get.call_count == 2
    assert result.status_code == 200


# --- connection / timeout retry path --------------------------------------


def test_retries_on_timeout_then_reraises_last():
    # Arrange — persistent timeouts exhaust retries and re-raise (same as before)
    with patch.object(
        http_retry.requests, "get", side_effect=requests.exceptions.Timeout("slow")
    ) as mock_get:
        # Act / Assert
        with pytest.raises(requests.exceptions.Timeout):
            get_with_retries("http://x")
    assert mock_get.call_count == 3


def test_retries_on_connection_error_then_reraises_last():
    # Arrange
    with patch.object(
        http_retry.requests,
        "get",
        side_effect=requests.exceptions.ConnectionError("no route"),
    ) as mock_get:
        # Act / Assert
        with pytest.raises(requests.exceptions.ConnectionError):
            get_with_retries("http://x")
    assert mock_get.call_count == 3


def test_succeeds_after_transient_connection_error():
    # Arrange — one ConnectionError then success
    with patch.object(
        http_retry.requests,
        "get",
        side_effect=[requests.exceptions.ConnectionError("blip"), _response(200)],
    ) as mock_get:
        # Act
        result = get_with_retries("http://x")
    # Assert
    assert mock_get.call_count == 2
    assert result.status_code == 200
