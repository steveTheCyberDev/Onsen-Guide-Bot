"""Unit tests for services.chat.chat_service.

The store is now SQLAlchemy-Core-backed (V3 Step 0), so these tests exercise it
through the PUBLIC seam (get_history / save_message) against a throwaway SQLite
file, never poking module internals. The `session_db` fixture repoints
`settings.session_db_url` at a per-test temp DB and rebuilds the engine so tests
are isolated and leave no shared state.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core.config import settings
from services.chat import chat_service


@pytest.fixture(autouse=True)
def session_db(tmp_path, monkeypatch):
    """Point the session store at a fresh throwaway SQLite file per test.

    Rebuilds the engine before AND after so no test leaks rows into another and
    the real local sessions.db is never touched. Using a file (not :memory:) also
    keeps the engine's connections consistent across the calls within a test.
    """
    db_file = tmp_path / "sessions_test.db"
    monkeypatch.setattr(settings, "session_db_url", f"sqlite:///{db_file}")
    chat_service._reset_engine_for_tests()
    yield
    chat_service._reset_engine_for_tests()


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
    # Arrange / Act — a read-only access must not register the session.
    chat_service.get_history("ghost")
    # Assert — no session row and no messages were created by the read.
    from sqlalchemy import func, select

    engine = chat_service._get_engine()
    with engine.connect() as conn:
        session_count = conn.execute(
            select(func.count()).select_from(chat_service.chat_sessions)
        ).scalar_one()
        message_count = conn.execute(
            select(func.count()).select_from(chat_service.chat_messages)
        ).scalar_one()
    assert session_count == 0
    assert message_count == 0


def test_history_survives_engine_restart():
    """History is durable: a fresh engine (simulated restart) reads back rows.

    Saves a turn, disposes/rebuilds the engine pointing at the SAME SQLite file,
    then asserts the messages replay — proving persistence, not in-memory state.
    """
    # Arrange
    chat_service.save_message("persist", human="still here?", ai="yes, on disk")
    # Act — simulate a process restart: drop the engine, next call rebuilds it
    # against the same session_db_url file.
    chat_service._reset_engine_for_tests()
    history = chat_service.get_history("persist")
    # Assert
    assert [m.content for m in history] == ["still here?", "yes, on disk"]
