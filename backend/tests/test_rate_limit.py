"""API tests for inbound rate limiting (api.limiter + main wiring + routes).

The slowapi limit string is captured at decoration time
(`@limiter.limit(settings.rate_limit_chat)`), so to exercise a *low* limit the
limiter and the route/app modules must be reloaded AFTER the relevant settings
are lowered. The `low_limit_client` fixture does exactly that, and resets the
limiter's in-memory storage so cases don't bleed into one another.

All route handlers are mocked at their point of use so no real agent/LLM/Rakuten
calls happen — these tests exercise the limiter, not the business logic.
"""

import importlib
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tests.conftest import TEST_API_KEY


@pytest.fixture
def low_limit_client(monkeypatch):
    """Fresh TestClient whose /chat and /hotels limits are very low.

    Sets the limit strings to 2/minute, then reloads the limiter, both route
    modules, and the app so the `@limiter.limit(...)` decorators re-bind to the
    low values. Yields the client plus a handle on the reloaded route modules so
    individual tests can patch the handler dependencies (run_agent / search_hotels).
    """
    from fastapi.testclient import TestClient

    import api.limiter as limiter_mod
    import api.main as main_mod
    import api.routes.chat as chat_mod
    import api.routes.hotels as hotels_mod
    from core.config import settings

    # Lower the limits BEFORE reloading so the decorators capture the low values.
    monkeypatch.setattr(settings, "rate_limit_chat", "2/minute", raising=False)
    monkeypatch.setattr(settings, "rate_limit_hotels", "2/minute", raising=False)

    # Reload in dependency order: limiter first, then routes, then app wiring.
    importlib.reload(limiter_mod)
    importlib.reload(chat_mod)
    importlib.reload(hotels_mod)
    importlib.reload(main_mod)

    # Clear any in-memory counters left from prior tests / app construction.
    main_mod.app.state.limiter.reset()

    with TestClient(main_mod.app, headers={"X-API-Key": TEST_API_KEY}) as tc:
        yield tc, chat_mod, hotels_mod

    # Restore the original modules so other test files see the unmodified app.
    importlib.reload(limiter_mod)
    importlib.reload(chat_mod)
    importlib.reload(hotels_mod)
    importlib.reload(main_mod)


# --- /chat rate limiting ---------------------------------------------------


def test_chat_within_limit_returns_200(low_limit_client):
    # Arrange
    tc, chat_mod, _ = low_limit_client
    agent_result = {"reply": "ok", "onsens": [], "hotels": []}
    # Act
    with patch.object(chat_mod, "run_agent", new=AsyncMock(return_value=agent_result)):
        first = tc.post("/chat", json={"message": "hi"})
        second = tc.post("/chat", json={"message": "hi"})
    # Assert — first two requests are under the 2/minute limit
    assert first.status_code == 200
    assert second.status_code == 200


def test_chat_exceeding_limit_returns_429(low_limit_client):
    # Arrange
    tc, chat_mod, _ = low_limit_client
    agent_result = {"reply": "ok", "onsens": [], "hotels": []}
    # Act — third request in the same window trips the limit
    with patch.object(chat_mod, "run_agent", new=AsyncMock(return_value=agent_result)):
        tc.post("/chat", json={"message": "hi"})
        tc.post("/chat", json={"message": "hi"})
        third = tc.post("/chat", json={"message": "hi"})
    # Assert
    assert third.status_code == 429


def test_chat_429_body_is_rate_limit_exceeded(low_limit_client):
    # Arrange
    tc, chat_mod, _ = low_limit_client
    agent_result = {"reply": "ok", "onsens": [], "hotels": []}
    # Act
    with patch.object(chat_mod, "run_agent", new=AsyncMock(return_value=agent_result)):
        for _ in range(3):
            resp = tc.post("/chat", json={"message": "hi"})
    # Assert
    assert resp.status_code == 429
    assert resp.json() == {"detail": "rate limit exceeded"}


# --- /hotels rate limiting -------------------------------------------------


def test_hotels_exceeding_limit_returns_429(low_limit_client):
    # Arrange
    tc, _, hotels_mod = low_limit_client
    body = {"latitude": 26.2124, "longitude": 127.6809, "radius": 3}
    # Act
    with patch.object(hotels_mod, "search_hotels", new=Mock(return_value=[])):
        tc.post("/hotels", json=body)
        tc.post("/hotels", json=body)
        third = tc.post("/hotels", json=body)
    # Assert
    assert third.status_code == 429
    assert third.json() == {"detail": "rate limit exceeded"}


def test_hotels_within_limit_returns_200(low_limit_client):
    # Arrange
    tc, _, hotels_mod = low_limit_client
    body = {"latitude": 26.2124, "longitude": 127.6809, "radius": 3}
    # Act
    with patch.object(hotels_mod, "search_hotels", new=Mock(return_value=[])):
        first = tc.post("/hotels", json=body)
        second = tc.post("/hotels", json=body)
    # Assert
    assert first.status_code == 200
    assert second.status_code == 200


# --- /health is never limited ---------------------------------------------


def test_health_is_never_rate_limited(low_limit_client):
    # Arrange
    tc, _, _ = low_limit_client
    # Act — far exceed the 2/minute chat/hotels limit on /health
    statuses = [tc.get("/health").status_code for _ in range(10)]
    # Assert — every single call succeeds; /health carries no limiter decorator
    assert statuses == [200] * 10


def test_chat_and_hotels_limits_are_independent(low_limit_client):
    # Arrange — exhausting /chat must not consume the /hotels budget
    tc, chat_mod, hotels_mod = low_limit_client
    agent_result = {"reply": "ok", "onsens": [], "hotels": []}
    body = {"latitude": 26.2124, "longitude": 127.6809, "radius": 3}
    # Act
    with patch.object(chat_mod, "run_agent", new=AsyncMock(return_value=agent_result)):
        for _ in range(3):
            tc.post("/chat", json={"message": "hi"})  # last one is 429
    with patch.object(hotels_mod, "search_hotels", new=Mock(return_value=[])):
        hotels_resp = tc.post("/hotels", json=body)
    # Assert — /hotels still has budget despite /chat being exhausted
    assert hotels_resp.status_code == 200
