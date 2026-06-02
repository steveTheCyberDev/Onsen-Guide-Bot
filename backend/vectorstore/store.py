import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

from core.config import settings

_client = None
_collection = None

COLLECTION_NAME = "onsen_springs"


def get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=settings.chroma_path)
    return _client


def get_collection():
    global _collection
    if _collection is None:
        embedding_fn = OpenAIEmbeddingFunction(
            api_key=settings.openai_api_key,
            model_name="text-embedding-3-small",
        )
        _collection = get_client().get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=embedding_fn,
        )
    return _collection
