"""API tests for POST /chat (api.routes.chat).

`run_agent` is async and is imported into the chat route module, so it is
patched there with an AsyncMock to isolate route logic from the LLM/agent.
"""

from unittest.mock import AsyncMock, patch

from core.exceptions import RakutenError


def test_chat_returns_200_with_reply(client):
    # Arrange
    agent_result = {"reply": "Found 1 onsen in Naha.", "onsens": [], "hotels": []}
    # Act
    with patch("api.routes.chat.run_agent", new=AsyncMock(return_value=agent_result)):
        response = client.post("/chat", json={"message": "onsen in naha", "session_id": "s1"})
    # Assert
    assert response.status_code == 200


def test_chat_response_contains_reply_text(client):
    # Arrange
    agent_result = {"reply": "Found 1 onsen in Naha.", "onsens": [], "hotels": []}
    # Act
    with patch("api.routes.chat.run_agent", new=AsyncMock(return_value=agent_result)):
        response = client.post("/chat", json={"message": "onsen in naha"})
    # Assert
    assert response.json()["reply"] == "Found 1 onsen in Naha."


def test_chat_response_shape_includes_onsens_and_hotels(client):
    # Arrange
    agent_result = {"reply": "ok", "onsens": [], "hotels": []}
    # Act
    with patch("api.routes.chat.run_agent", new=AsyncMock(return_value=agent_result)):
        body = client.post("/chat", json={"message": "hi"}).json()
    # Assert
    assert "onsens" in body and "hotels" in body


def test_chat_uses_default_session_id_when_omitted(client):
    # Arrange
    agent_result = {"reply": "ok", "onsens": [], "hotels": []}
    mock_agent = AsyncMock(return_value=agent_result)
    # Act
    with patch("api.routes.chat.run_agent", new=mock_agent):
        client.post("/chat", json={"message": "hi"})
    # Assert — second positional arg is session_id, defaulting to "default"
    assert mock_agent.await_args.args[1] == "default"


def test_chat_returns_422_when_message_missing(client):
    # Arrange / Act
    response = client.post("/chat", json={"session_id": "s1"})
    # Assert
    assert response.status_code == 422


def test_chat_returns_502_on_onsenbot_error(client):
    # Arrange — RakutenError subclasses OnsenBotError
    failing = AsyncMock(side_effect=RakutenError("Rakuten is down"))
    # Act
    with patch("api.routes.chat.run_agent", new=failing):
        response = client.post("/chat", json={"message": "hi"})
    # Assert
    assert response.status_code == 502


def test_chat_502_detail_carries_error_message(client):
    # Arrange
    failing = AsyncMock(side_effect=RakutenError("Rakuten is down"))
    # Act
    with patch("api.routes.chat.run_agent", new=failing):
        response = client.post("/chat", json={"message": "hi"})
    # Assert
    assert response.json()["detail"] == "Rakuten is down"


def test_chat_returns_500_on_unexpected_exception(client):
    # Arrange
    failing = AsyncMock(side_effect=ValueError("boom"))
    # Act
    with patch("api.routes.chat.run_agent", new=failing):
        response = client.post("/chat", json={"message": "hi"})
    # Assert
    assert response.status_code == 500


def test_chat_500_detail_is_generic(client):
    # Arrange — internal error details must not leak to the client
    failing = AsyncMock(side_effect=ValueError("secret internal trace"))
    # Act
    with patch("api.routes.chat.run_agent", new=failing):
        response = client.post("/chat", json={"message": "hi"})
    # Assert
    assert response.json()["detail"] == "An unexpected error occurred."
