from langchain_core.tools import tool

from services.retrieval.retrieval_service import query_onsen


@tool
def search_onsen(query: str, prefecture: str | None = None) -> str:
    """Search the onsen database for hot springs matching the query.

    Args:
        query: A free-text description of what the user is looking for
            (e.g. "relaxing sulfur spring", "family-friendly onsen").
        prefecture: The English name of the prefecture to restrict results to
            (e.g. "Okinawa", "Mie", "Tokyo"). Pass this whenever the user names
            a location/region so results are limited to that prefecture. Omit it
            (leave as None) when the user does not specify a location.
    """
    return query_onsen(query, prefecture=prefecture)
