from langchain_core.tools import tool

from services.retrieval.retrieval_service import query_onsen


@tool
def search_onsen(query: str) -> str:
    """Search the onsen database for hot springs matching the query."""
    return query_onsen(query)
