from vectorstore.store import get_collection


def query_onsen(query: str, n_results: int = 20, prefecture: str | None = None) -> str:
    """Search the onsen vector store, optionally constrained to a prefecture.

    Args:
        query: Free-text semantic query (e.g. "relaxing sulfur spring").
        n_results: Maximum number of onsen to return. Defaults to 20 so broad
            requests ("all onsen in Shizuoka") surface a useful list rather than
            just the top few; kept bounded to keep the context block sent to the
            LLM a reasonable size.
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
        block = (
            f"Name: {meta.get('name_en', meta.get('name', ''))}\n"
            f"Location: {meta.get('city_en', '')}, {meta.get('prefecture_en', '')}\n"
            f"Spring type: {meta.get('spa_quality_en', '')}\n"
            f"Description: {doc}\n"
            f"URL: {meta.get('detail_url', '')}"
        )
        # Coordinates are stored at ingest time (see scripts/ingest.py) but only
        # for records that were successfully geocoded. Surface them so the agent
        # can carry them through to the response verbatim instead of re-geocoding
        # at request time. Both keys must be present, or neither line is emitted.
        if "latitude" in meta and "longitude" in meta:
            block += f"\nLatitude: {meta['latitude']}\nLongitude: {meta['longitude']}"
        output.append(block)
    return "\n\n---\n\n".join(output) if output else "No onsen found matching your query."
