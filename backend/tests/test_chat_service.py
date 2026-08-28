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


# ---------------------------------------------------------------------------
# Edge-case coverage (V3 Step 0): ordering at depth, content fidelity, message
# type fidelity, isolation at scale, multi-session durability, idempotent schema
# init, and single-session-row creation semantics. These extend — and never
# duplicate or weaken — the happy-path tests above.
# ---------------------------------------------------------------------------


def test_ordering_preserved_across_many_turns():
    """5 turns in one session replay strictly human,ai,human,ai,... in order.

    Guards against any ordering regression that only shows up beyond 1-2 turns
    (e.g. if replay ever ordered by timestamp, which can collide, instead of the
    autoincrement id).
    """
    # Arrange — 5 turns with content that encodes its own position.
    for i in range(1, 6):
        chat_service.save_message("deep", human=f"q{i}", ai=f"a{i}")
    # Act
    history = chat_service.get_history("deep")
    # Assert — exact interleaved chronological sequence across all 10 messages.
    assert [m.content for m in history] == [
        "q1", "a1", "q2", "a2", "q3", "a3", "q4", "a4", "q5", "a5",
    ]


def test_message_types_alternate_across_many_turns():
    """Across many turns every even index is a HumanMessage, every odd an AIMessage.

    Content equality alone would not catch a role/type mix-up, so assert the
    reconstructed LangChain types position-by-position.
    """
    # Arrange
    for i in range(4):
        chat_service.save_message("types", human=f"h{i}", ai=f"a{i}")
    # Act
    history = chat_service.get_history("types")
    # Assert
    assert len(history) == 8
    for idx, msg in enumerate(history):
        if idx % 2 == 0:
            assert isinstance(msg, HumanMessage), f"index {idx} should be human"
        else:
            assert isinstance(msg, AIMessage), f"index {idx} should be ai"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty-string"),
        pytest.param("草津温泉はどこですか？", id="japanese-unicode"),
        pytest.param("line1\nline2\r\nline3\ttabbed", id="newlines-and-tabs"),
        pytest.param("Robert'); DROP TABLE chat_messages;--", id="sql-injection-ish"),
        pytest.param('he said "onsen" and \'yudedako\'', id="mixed-quotes"),
        pytest.param("温泉 " * 20000, id="very-long-content"),
    ],
)
def test_content_stored_and_returned_verbatim(content):
    """Arbitrary content in both human and ai is round-tripped byte-for-byte.

    Covers empty strings, Japanese unicode, whitespace/newlines, quote and
    SQL-ish characters, and very long payloads — the store must never mangle,
    truncate, or interpret content.
    """
    # Arrange / Act
    chat_service.save_message("fidelity", human=content, ai=content)
    history = chat_service.get_history("fidelity")
    # Assert — both roles preserved verbatim.
    assert history[0].content == content
    assert history[1].content == content


def test_isolation_holds_under_interleaved_multi_session_writes():
    """Interleaved writes to 3 sessions never bleed into each other's history.

    Writes turns to sessions a/b/c out of order; each get_history must return
    only that session's turns, in that session's own chronological order.
    """
    # Arrange — interleave writes across three sessions.
    chat_service.save_message("a", human="a1", ai="ra1")
    chat_service.save_message("b", human="b1", ai="rb1")
    chat_service.save_message("c", human="c1", ai="rc1")
    chat_service.save_message("a", human="a2", ai="ra2")
    chat_service.save_message("b", human="b2", ai="rb2")
    # Act / Assert — each session sees only and exactly its own turns, in order.
    assert [m.content for m in chat_service.get_history("a")] == [
        "a1", "ra1", "a2", "ra2",
    ]
    assert [m.content for m in chat_service.get_history("b")] == [
        "b1", "rb1", "b2", "rb2",
    ]
    assert [m.content for m in chat_service.get_history("c")] == ["c1", "rc1"]


def test_multi_session_history_survives_engine_restart():
    """Durability across a restart holds for several sessions at once.

    Extends test_history_survives_engine_restart to prove no session is lost or
    cross-contaminated when the engine is rebuilt against the same file.
    """
    # Arrange
    chat_service.save_message("s1", human="one", ai="r-one")
    chat_service.save_message("s2", human="two", ai="r-two")
    chat_service.save_message("s1", human="one-again", ai="r-one-again")
    # Act — simulate a process restart.
    chat_service._reset_engine_for_tests()
    # Assert — both sessions read back intact and separate.
    assert [m.content for m in chat_service.get_history("s1")] == [
        "one", "r-one", "one-again", "r-one-again",
    ]
    assert [m.content for m in chat_service.get_history("s2")] == ["two", "r-two"]


def test_schema_init_is_idempotent_and_preserves_data():
    """Re-running create_all against the same populated file is a no-op, not a wipe.

    _get_engine calls metadata.create_all on every rebuild; this asserts that a
    second create_all neither errors nor drops existing rows (guards the
    'existing DB is left untouched' contract).
    """
    # Arrange — populate, then rebuild the engine (which re-runs create_all).
    chat_service.save_message("idem", human="keep me", ai="still here")
    chat_service._reset_engine_for_tests()
    engine = chat_service._get_engine()
    # Act — explicitly invoke create_all a second time on the live engine.
    chat_service.metadata.create_all(engine)  # must not raise or wipe
    # Assert — original turn survives both the rebuild and the extra create_all.
    assert [m.content for m in chat_service.get_history("idem")] == [
        "keep me", "still here",
    ]


def test_two_turns_create_exactly_one_session_row():
    """Two turns in one session yield exactly one chat_sessions row (not two).

    Guards the exists-then-insert logic in save_message: the session row is
    created on the first turn only, while both turns still append their messages.
    """
    from sqlalchemy import func, select

    # Arrange / Act — two turns for the same session.
    chat_service.save_message("once", human="t1", ai="r1")
    chat_service.save_message("once", human="t2", ai="r2")
    # Assert — one session row, four message rows.
    engine = chat_service._get_engine()
    with engine.connect() as conn:
        session_count = conn.execute(
            select(func.count()).select_from(chat_service.chat_sessions)
        ).scalar_one()
        message_count = conn.execute(
            select(func.count()).select_from(chat_service.chat_messages)
        ).scalar_one()
    assert session_count == 1
    assert message_count == 4
