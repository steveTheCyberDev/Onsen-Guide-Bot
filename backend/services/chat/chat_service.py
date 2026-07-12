"""Durable chat session store (V3 Step 0).

Replaces the former in-memory ``_history: dict`` with a SQLAlchemy-Core-backed
store so multi-turn conversation history survives a process restart and can be
shared across workers/instances. The public seam is unchanged:

    get_history(session_id)          -> list[BaseMessage]  (chronological)
    save_message(session_id, human, ai) -> None            (one turn)

so callers (``agent/workflow/pipeline.py``) need no changes.

The dialect is selected entirely by the connection URL
(``settings.resolved_session_db_url``): a local SQLite file by default, a Postgres
URL in production. One set of Table definitions + queries serves both engines.
"""

import logging

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import (
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Column,
    create_engine,
    func,
    insert,
    select,
)
from sqlalchemy.engine import Engine

from core.config import settings

logger = logging.getLogger(__name__)

# Role labels stored in chat_messages.role. Exactly two values:
#   "human" -> reconstructed as a LangChain HumanMessage
#   "ai"    -> reconstructed as a LangChain AIMessage
_ROLE_HUMAN = "human"
_ROLE_AI = "ai"

metadata = MetaData()

# One row per conversation. Created lazily the first time a turn is saved for the
# session (a read never creates a row — see get_history).
chat_sessions = Table(
    "chat_sessions",
    metadata,
    Column("session_id", String, primary_key=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
)

# One row per message. Ordering is deterministic by the autoincrement ``id``
# (human row is inserted before the ai row within a turn), which is stable even
# if two rows share a created_at timestamp.
chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String, index=True, nullable=False),
    Column("role", String, nullable=False),
    Column("content", Text, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
)

# Module-level engine singleton. Built lazily on first use so importing this
# module has no side effects (matches the old in-memory module) and so tests can
# repoint settings.session_db_url before the engine is created.
_engine: Engine | None = None


def _get_engine() -> Engine:
    """Return the process-wide engine, creating it + the schema on first use.

    ``metadata.create_all`` is idempotent, so a fresh local SQLite file works with
    zero manual setup and an existing DB is left untouched.
    """
    global _engine
    if _engine is None:
        url = settings.resolved_session_db_url
        _engine = create_engine(url, future=True)
        metadata.create_all(_engine)
        # Log the dialect/backend, never the full URL (a Postgres URL carries
        # credentials).
        logger.info("chat session store initialized backend=%s", _engine.dialect.name)
    return _engine


def _reset_engine_for_tests() -> None:
    """Dispose and clear the engine singleton so the next call rebuilds it.

    Used by the test suite to repoint ``settings.session_db_url`` at a throwaway
    SQLite database between tests. Not used in production code.
    """
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def get_history(session_id: str) -> list[BaseMessage]:
    """Return the stored conversation for ``session_id`` in chronological order.

    Messages are replayed as LangChain ``HumanMessage``/``AIMessage`` objects
    ordered by insertion id (human then ai per turn). An unknown session returns
    an empty list and does NOT create any state (read-only SELECT only).
    """
    engine = _get_engine()
    stmt = (
        select(chat_messages.c.role, chat_messages.c.content)
        .where(chat_messages.c.session_id == session_id)
        .order_by(chat_messages.c.id)
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()

    messages: list[BaseMessage] = []
    for role, content in rows:
        if role == _ROLE_HUMAN:
            messages.append(HumanMessage(content=content))
        elif role == _ROLE_AI:
            messages.append(AIMessage(content=content))
        else:
            # Defensive: an unexpected role means a schema/data drift bug, not a
            # user error — log and skip rather than crash the whole reply.
            logger.warning(
                "skipping chat_messages row with unknown role=%r session_id=%s",
                role,
                session_id,
            )
    return messages


def save_message(session_id: str, human: str, ai: str) -> None:
    """Persist one conversation turn: the human message then the ai reply.

    Creates the ``chat_sessions`` row on first turn for the session. Both message
    rows are written in a single transaction so a turn is never half-persisted.
    """
    engine = _get_engine()
    with engine.begin() as conn:
        # Create the session row on first turn. A dialect-agnostic
        # exists-then-insert (instead of an ON CONFLICT upsert) keeps this working
        # identically on both SQLite and Postgres. The surrounding transaction
        # makes the check-then-insert atomic for a single writer.
        exists = conn.execute(
            select(chat_sessions.c.session_id).where(
                chat_sessions.c.session_id == session_id
            )
        ).first()
        if exists is None:
            conn.execute(insert(chat_sessions).values(session_id=session_id))

        conn.execute(
            insert(chat_messages),
            [
                {"session_id": session_id, "role": _ROLE_HUMAN, "content": human},
                {"session_id": session_id, "role": _ROLE_AI, "content": ai},
            ],
        )
