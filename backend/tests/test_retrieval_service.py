"""Unit tests for services.retrieval.retrieval_service.

ChromaDB is never touched: `get_collection` is mocked to return a fake
collection whose `.query` yields canned documents + metadata. The module
imports the symbol via `from vectorstore.store import get_collection`, so the
patch target is the name bound inside the retrieval module.
"""

from unittest.mock import MagicMock, patch

from services.retrieval import retrieval_service


def _fake_collection(documents, metadatas):
    collection = MagicMock()
    collection.query.return_value = {
        "documents": [documents],
        "metadatas": [metadatas],
    }
    return collection


def test_query_onsen_formats_single_result():
    # Arrange
    docs = ["A relaxing sulfur spring."]
    metas = [
        {
            "name_en": "Beppu Onsen",
            "city_en": "Beppu",
            "prefecture_en": "Oita",
            "spa_quality_en": "Sulfur spring",
            "detail_url": "https://example.com/beppu",
        }
    ]
    # Act
    with patch.object(retrieval_service, "get_collection", return_value=_fake_collection(docs, metas)):
        result = retrieval_service.query_onsen("relaxing spring")
    # Assert
    assert "Name: Beppu Onsen" in result


def test_query_onsen_includes_description_and_url():
    # Arrange
    docs = ["A relaxing sulfur spring."]
    metas = [{"name_en": "Beppu Onsen", "detail_url": "https://example.com/beppu"}]
    # Act
    with patch.object(retrieval_service, "get_collection", return_value=_fake_collection(docs, metas)):
        result = retrieval_service.query_onsen("spring")
    # Assert
    assert "https://example.com/beppu" in result and "A relaxing sulfur spring." in result


def test_query_onsen_joins_multiple_results_with_separator():
    # Arrange
    docs = ["doc one", "doc two"]
    metas = [{"name_en": "One"}, {"name_en": "Two"}]
    # Act
    with patch.object(retrieval_service, "get_collection", return_value=_fake_collection(docs, metas)):
        result = retrieval_service.query_onsen("spring")
    # Assert
    assert "\n\n---\n\n" in result


def test_query_onsen_falls_back_to_name_when_name_en_missing():
    # Arrange — only Japanese `name` present
    docs = ["doc"]
    metas = [{"name": "別府温泉"}]
    # Act
    with patch.object(retrieval_service, "get_collection", return_value=_fake_collection(docs, metas)):
        result = retrieval_service.query_onsen("spring")
    # Assert
    assert "Name: 別府温泉" in result


def test_query_onsen_returns_no_match_message_when_empty():
    # Arrange
    with patch.object(retrieval_service, "get_collection", return_value=_fake_collection([], [])):
        # Act
        result = retrieval_service.query_onsen("nothing here")
    # Assert
    assert result == "No onsen found matching your query."


def test_query_onsen_passes_n_results_to_collection():
    # Arrange
    collection = _fake_collection([], [])
    # Act
    with patch.object(retrieval_service, "get_collection", return_value=collection):
        retrieval_service.query_onsen("spring", n_results=3)
    # Assert
    _, kwargs = collection.query.call_args
    assert kwargs["n_results"] == 3
