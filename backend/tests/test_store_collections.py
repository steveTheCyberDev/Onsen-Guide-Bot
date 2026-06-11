"""Tests for the per-name collection caching in vectorstore.store.

Covers the Step C generalization: get_collection() with no args still returns
the onsen collection (behaviour-identical for existing callers), and
get_kb_collection() requests the separate KB collection name. The chroma client
and embedding fn are mocked so no real DB or OpenAI call happens.
"""

from unittest.mock import MagicMock, patch

import vectorstore.store as store
from core.config import settings


def _reset():
    store._client = None
    store._collections = {}


def test_get_collection_no_args_requests_onsen_collection():
    # Arrange
    _reset()
    fake_client = MagicMock()
    # Act
    with patch.object(store, "get_client", return_value=fake_client), \
            patch.object(store, "OpenAIEmbeddingFunction"):
        store.get_collection()
    # Assert — the no-arg default still targets the onsen collection name.
    _, kwargs = fake_client.get_or_create_collection.call_args
    assert kwargs["name"] == store.COLLECTION_NAME == "onsen_springs"
    _reset()


def test_get_kb_collection_requests_kb_collection_name():
    # Arrange
    _reset()
    fake_client = MagicMock()
    # Act
    with patch.object(store, "get_client", return_value=fake_client), \
            patch.object(store, "OpenAIEmbeddingFunction"):
        store.get_kb_collection()
    # Assert — KB accessor targets settings.kb_collection (a SEPARATE collection).
    _, kwargs = fake_client.get_or_create_collection.call_args
    assert kwargs["name"] == settings.kb_collection
    _reset()


def test_collections_cached_per_name_independently():
    # Arrange — onsen and KB collections are distinct singletons; the client is
    # asked to create each exactly once even across repeat calls.
    _reset()
    fake_client = MagicMock()
    fake_client.get_or_create_collection.side_effect = [
        MagicMock(name="onsen"),
        MagicMock(name="kb"),
    ]
    # Act
    with patch.object(store, "get_client", return_value=fake_client), \
            patch.object(store, "OpenAIEmbeddingFunction"):
        onsen_a = store.get_collection()
        onsen_b = store.get_collection()
        kb_a = store.get_kb_collection()
        kb_b = store.get_kb_collection()
    # Assert — two distinct cached objects, two creation calls total (one per name).
    assert onsen_a is onsen_b
    assert kb_a is kb_b
    assert onsen_a is not kb_a
    assert fake_client.get_or_create_collection.call_count == 2
    _reset()
