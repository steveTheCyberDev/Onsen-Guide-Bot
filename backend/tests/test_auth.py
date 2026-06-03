"""Auth tests for the X-API-Key guard on /chat and /hotels.

The guard is a router-level dependency (api.dependencies.require_api_key), so it
runs before any handler logic — these tests need no agent/service mocking.
/health must stay open.
"""

from unittest.mock import AsyncMock, patch

from tests.conftest import TEST_API_KEY


def test_chat_without_key_returns_401(unauth_client):
    response = unauth_client.post("/chat", json={"message": "hi"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API key"


def test_chat_with_wrong_key_returns_401(unauth_client):
    response = unauth_client.post(
        "/chat", json={"message": "hi"}, headers={"X-API-Key": "wrong-key"}
    )
    assert response.status_code == 401


def test_hotels_without_key_returns_401(unauth_client):
    response = unauth_client.post(
        "/hotels", json={"latitude": 26.2, "longitude": 127.7}
    )
    assert response.status_code == 401


def test_hotels_with_wrong_key_returns_401(unauth_client):
    response = unauth_client.post(
        "/hotels",
        json={"latitude": 26.2, "longitude": 127.7},
        headers={"X-API-Key": "wrong-key"},
    )
    assert response.status_code == 401


def test_chat_with_valid_key_passes_guard(unauth_client):
    # A valid key must reach the handler — mock the agent so no LLM call is made.
    agent_result = {"reply": "ok", "onsens": [], "hotels": []}
    with patch("api.routes.chat.run_agent", new=AsyncMock(return_value=agent_result)):
        response = unauth_client.post(
            "/chat", json={"message": "hi"}, headers={"X-API-Key": TEST_API_KEY}
        )
    assert response.status_code == 200


def test_health_is_open_without_key(unauth_client):
    response = unauth_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
