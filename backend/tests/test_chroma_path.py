"""Regression tests for the ChromaDB path being a single source of truth.

Background: the app (vectorstore.store) and the ingest job (scripts.ingest) used
to compute the Chroma path differently. In the Railway container that made ingest
write to a throwaway directory while the app read an empty volume, so /chat
returned zero results. Both now read settings.chroma_path.

chromadb is mocked so these tests never create a real DB or hit OpenAI.
"""

from unittest.mock import MagicMock, patch

import vectorstore.store as store
from core.config import settings


def _reset_store_singletons():
    """Clear the module-level client/collection caches between assertions."""
    store._client = None
    store._collections = {}


def test_get_client_opens_settings_chroma_path():
    # Arrange
    _reset_store_singletons()
    # Act
    with patch.object(store.chromadb, "PersistentClient") as mock_client:
        store.get_client()
    # Assert — the client is opened at exactly settings.chroma_path
    mock_client.assert_called_once_with(path=settings.chroma_path)


def test_overriding_chroma_path_changes_where_chroma_opens():
    # Arrange — simulate the production override (Railway: CHROMA_PATH=/app/chroma_db)
    _reset_store_singletons()
    override = "/app/chroma_db"
    # Act
    with patch.object(settings, "chroma_path", override), \
            patch.object(store.chromadb, "PersistentClient") as mock_client:
        store.get_client()
    # Assert
    mock_client.assert_called_once_with(path=override)
    _reset_store_singletons()


def test_ingest_and_app_resolve_the_same_collection():
    """The ingest job must obtain its collection from vectorstore.store, so it
    can never diverge from the path/collection the app uses."""
    # Arrange
    from scripts import ingest
    # Assert — ingest references the store's get_collection, not a private copy
    assert ingest.get_collection is store.get_collection


def test_ingest_writes_to_the_overridden_path():
    """End-to-end-ish: with CHROMA_PATH overridden, the collection ingest writes
    to is opened at that same path (proving app/ingest agreement)."""
    # Arrange
    _reset_store_singletons()
    override = "/app/chroma_db"
    from scripts import ingest

    fake_client = MagicMock()
    with patch.object(settings, "chroma_path", override), \
            patch.object(store.chromadb, "PersistentClient", return_value=fake_client) as mock_client, \
            patch.object(store, "OpenAIEmbeddingFunction"):
        # Act — ingest's get_collection is the store's, which calls get_client()
        ingest.get_collection()
    # Assert
    mock_client.assert_called_once_with(path=override)
    _reset_store_singletons()
