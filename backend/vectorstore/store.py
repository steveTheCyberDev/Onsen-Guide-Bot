import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

from core.config import settings

_client = None
# Per-name collection cache. Each distinct collection name (e.g. the onsen
# "onsen_springs" and the Layer 2 KB "onsen_knowledge") is a separate singleton
# so they stay structurally isolated — an onsen query can never surface a KB
# chunk and vice-versa, and eval_flow.build_ground_truth never sees KB metadata.
_collections: dict[str, object] = {}

COLLECTION_NAME = "onsen_springs"


def get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=settings.chroma_path)
    return _client


def get_collection(name: str = COLLECTION_NAME):
    """Return the named ChromaDB collection, cached per-name.

    The no-arg call returns the onsen ``onsen_springs`` collection and behaves
    exactly as before (callers in retrieval_service, scripts/ingest, and
    scripts/eval_flow rely on this). All collections share the same
    ``text-embedding-3-small`` embedding function.
    """
    if name not in _collections:
        embedding_fn = OpenAIEmbeddingFunction(
            api_key=settings.openai_api_key,
            model_name="text-embedding-3-small",
        )
        _collections[name] = get_client().get_or_create_collection(
            name=name,
            embedding_function=embedding_fn,
        )
    return _collections[name]


def get_kb_collection():
    """Return the Layer 2 knowledge-base collection (settings.kb_collection).

    Kept a SEPARATE collection from the onsen one on purpose: it makes onsen vs
    KB isolation structural (no doc_type filter to forget on the live search
    path), and keeps eval_flow.build_ground_truth from ever ingesting KB chunks
    as onsen ground truth.
    """
    return get_collection(settings.kb_collection)
