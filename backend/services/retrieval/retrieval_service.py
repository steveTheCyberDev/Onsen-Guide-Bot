from vectorstore.store import get_collection


def query_onsen(query: str, n_results: int = 5, prefecture: str | None = None) -> str:
    """Search the onsen vector store, optionally constrained to a prefecture.

    Args:
        query: Free-text semantic query (e.g. "relaxing sulfur spring").
        n_results: Maximum number of onsen to return.
        prefecture: Optional English prefecture name (e.g. "Okinawa"). When
            provided, results are filtered to that prefecture via a ChromaDB
            metadata `where` clause; semantic similarity alone does not
            constrain location, so this is required for location-specific
            requests. When omitted, no metadata filter is applied (the original
            pure-semantic behaviour).
    """
    collection = get_collection()
    query_kwargs: dict = {"query_texts": [query], "n_results": n_results}
    if prefecture:
        query_kwargs["where"] = {"prefecture_en": prefecture}
    results = collection.query(**query_kwargs)
    docs = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    output = []
    for doc, meta in zip(docs, metadatas):
        output.append(
            f"Name: {meta.get('name_en', meta.get('name', ''))}\n"
            f"Location: {meta.get('city_en', '')}, {meta.get('prefecture_en', '')}\n"
            f"Spring type: {meta.get('spa_quality_en', '')}\n"
            f"Description: {doc}\n"
            f"URL: {meta.get('detail_url', '')}"
        )
    return "\n\n---\n\n".join(output) if output else "No onsen found matching your query."
