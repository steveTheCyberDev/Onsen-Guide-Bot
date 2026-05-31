"""Unit tests for services.chat.chat_service.

Pure in-memory module — no external I/O to mock. The module keeps a single
module-level `_history` dict, so each test clears it first for isolation.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from services.chat import chat_service


@pytest.fixture(autouse=True)
def clear_history():
    """Reset the shared in-memory history before and after every test."""
    chat_service._history.clear()
    yield
    chat_service._history.clear()


def test_get_history_unknown_session_returns_empty_list():
    # Arrange / Act
    history = chat_service.get_history("never-seen")
    # Assert
    assert history == []


def test_save_message_appends_human_then_ai():
    # Arrange / Act
    chat_service.save_message("s1", human="Where is Naha?", ai="In Okinawa.")
    history = chat_service.get_history("s1")
    # Assert
    assert len(history) == 2


def test_save_message_stores_human_message_first():
    # Arrange / Act
    chat_service.save_message("s1", human="Where is Naha?", ai="In Okinawa.")
    first = chat_service.get_history("s1")[0]
    # Assert
    assert isinstance(first, HumanMessage)


def test_save_message_stores_ai_message_second():
    # Arrange / Act
    chat_service.save_message("s1", human="Where is Naha?", ai="In Okinawa.")
    second = chat_service.get_history("s1")[1]
    # Assert
    assert isinstance(second, AIMessage)


def test_save_message_preserves_human_content():
    # Arrange / Act
    chat_service.save_message("s1", human="Where is Naha?", ai="In Okinawa.")
    first = chat_service.get_history("s1")[0]
    # Assert
    assert first.content == "Where is Naha?"


def test_save_message_preserves_ai_content():
    # Arrange / Act
    chat_service.save_message("s1", human="Where is Naha?", ai="In Okinawa.")
    second = chat_service.get_history("s1")[1]
    # Assert
    assert second.content == "In Okinawa."


def test_multiple_saves_accumulate_in_order():
    # Arrange / Act
    chat_service.save_message("s1", human="first", ai="reply-1")
    chat_service.save_message("s1", human="second", ai="reply-2")
    history = chat_service.get_history("s1")
    # Assert
    assert [m.content for m in history] == ["first", "reply-1", "second", "reply-2"]


def test_sessions_are_isolated():
    # Arrange
    chat_service.save_message("session-a", human="hi a", ai="bye a")
    chat_service.save_message("session-b", human="hi b", ai="bye b")
    # Act
    history_a = chat_service.get_history("session-a")
    # Assert
    assert [m.content for m in history_a] == ["hi a", "bye a"]


def test_get_history_does_not_create_session():
    # Arrange / Act
    chat_service.get_history("ghost")
    # Assert — read-only access must not register the session
    assert "ghost" not in chat_service._history
