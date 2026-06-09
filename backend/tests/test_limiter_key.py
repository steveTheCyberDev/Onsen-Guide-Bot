"""Unit tests for the rate-limit key function (api.limiter._client_ip).

The key function picks the real client IP: the right-most X-Forwarded-For entry
(the address appended by our single trusted proxy) when present, falling back to
the socket peer (slowapi's get_remote_address) when the header is absent. No
network or app needed — a minimal fake Request with `.headers` and `.client` is
enough.
"""

from unittest.mock import patch

from api.limiter import _client_ip


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    """Minimal stand-in for starlette Request used by the key function."""

    def __init__(self, headers=None, client_host="127.0.0.1"):
        self.headers = headers or {}
        self.client = _FakeClient(client_host)


def test_picks_rightmost_xff_entry():
    # Arrange — client-supplied entries first, our trusted proxy appends last
    req = _FakeRequest(headers={"X-Forwarded-For": "203.0.113.7, 70.41.3.18, 150.172.238.178"})
    # Act
    key = _client_ip(req)
    # Assert — right-most is what the trusted proxy saw; left-most is forgeable
    assert key == "150.172.238.178"


def test_rightmost_resists_spoofed_leftmost_entry():
    # Arrange — a caller forges a left-most IP; only the right-most is trusted
    req = _FakeRequest(headers={"X-Forwarded-For": "1.2.3.4, 10.0.0.1"})
    # Act
    key = _client_ip(req)
    # Assert
    assert key == "10.0.0.1"


def test_strips_whitespace_around_rightmost_entry():
    # Arrange
    req = _FakeRequest(headers={"X-Forwarded-For": "10.0.0.1,   198.51.100.5  "})
    # Act
    key = _client_ip(req)
    # Assert
    assert key == "198.51.100.5"


def test_single_xff_entry_is_used_verbatim():
    # Arrange
    req = _FakeRequest(headers={"X-Forwarded-For": "192.0.2.44"})
    # Act
    key = _client_ip(req)
    # Assert
    assert key == "192.0.2.44"


def test_falls_back_to_socket_peer_when_xff_absent():
    # Arrange — no X-Forwarded-For header at all
    req = _FakeRequest(headers={}, client_host="10.1.2.3")
    # Act
    key = _client_ip(req)
    # Assert — slowapi's get_remote_address reads request.client.host
    assert key == "10.1.2.3"


def test_falls_back_to_socket_peer_when_xff_blank():
    # Arrange — header present but empty/blank should not be trusted
    req = _FakeRequest(headers={"X-Forwarded-For": "   "}, client_host="10.9.9.9")
    # Act
    key = _client_ip(req)
    # Assert — blank left-most entry → fall through to socket peer
    assert key == "10.9.9.9"


def test_falls_back_via_get_remote_address(monkeypatch):
    # Arrange — verify the fallback delegates to slowapi's get_remote_address
    req = _FakeRequest(headers={})
    with patch("api.limiter.get_remote_address", return_value="sentinel-ip") as gra:
        # Act
        key = _client_ip(req)
    # Assert
    assert key == "sentinel-ip"
    gra.assert_called_once_with(req)
