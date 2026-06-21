from vectorstore.store import get_collection, get_kb_collection


def _nonempty_query(query: str) -> str:
    """Guard against an empty/whitespace query before embedding.

    The embeddings backend 400s on an empty string ("input cannot be an empty
    string"). The intent parser can emit an empty semantic query for pure
    location listings ("top 5 onsen in Gifu"), where the prefecture `where`
    filter does the real work — fall back to a neutral term so the embedding
    call is always valid.
    """
    return (query or "").strip() or "onsen"


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
    query = _nonempty_query(query)
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


def query_onsen_structured(
    query: str, n_results: int = 20, prefecture: str | None = None
) -> list[dict]:
    """Same Chroma query as query_onsen, but returns structured records instead
    of a formatted string. Used by the V2 workflow to assemble onsens[] in
    Python with no LLM round-trip.

    Args:
        query: Free-text semantic query (e.g. "relaxing sulfur spring").
        n_results: Maximum number of onsen to return. Mirrors query_onsen.
        prefecture: Optional English prefecture name (e.g. "Okinawa"). When
            provided, results are filtered to that prefecture via a ChromaDB
            metadata `where` clause. When omitted, pure-semantic behaviour.

    Returns:
        A list of structured records (one dict per matching onsen). Each dict's
        keys mirror the agent's OnsenResult model plus description/detail_url so
        the V2 workflow can build OnsenResult(**record) and render results
        without an LLM round-trip. Empty result set returns [].
    """
    query = _nonempty_query(query)
    collection = get_collection()
    query_kwargs: dict = {"query_texts": [query], "n_results": n_results}
    if prefecture:
        query_kwargs["where"] = {"prefecture_en": prefecture}
    results = collection.query(**query_kwargs)
    docs = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    records: list[dict] = []
    for doc, meta in zip(docs, metadatas):
        # Build the composite location the same way the string path does, but
        # drop an empty side so we never emit a leading/trailing ", ".
        city = meta.get("city_en", "")
        prefecture_en = meta.get("prefecture_en", "")
        location = ", ".join(part for part in (city, prefecture_en) if part)

        # Coordinates are stored at ingest time but only for records that were
        # successfully geocoded. Both keys must be present, or both are None.
        if "latitude" in meta and "longitude" in meta:
            lat = meta["latitude"]
            lng = meta["longitude"]
        else:
            lat = None
            lng = None

        records.append(
            {
                "name": meta.get("name_en", meta.get("name", "")),
                "location": location,
                # spring_type is the short spring-type label (e.g. "Sulfur
                # Spring"); spa_quality carries the rich description text (the
                # embedded Chroma document, `doc`) so the user-facing field has
                # the full descriptive text, matching the legacy ReAct output.
                "spring_type": meta.get("spa_quality_en", ""),
                "spa_quality": doc,
                "detail_url": meta.get("detail_url"),
                "lat": lat,
                "lng": lng,
            }
        )
    return records


def query_knowledge_with_diagnostics(
    query: str, n_results: int, max_distance: float | None = None
) -> tuple[list[dict], dict]:
    """Semantic search over the Layer 2 KB collection, with retrieval diagnostics.

    Same query + threshold behaviour as ``query_knowledge``, but ALSO returns a
    diagnostics dict alongside the records so callers (ask-mode) can later
    distinguish a TRUE coverage gap (nothing relevant retrieved → high
    min_distance / nothing retrieved) from a FALSE refusal (a relevant chunk WAS
    retrieved but the grounding prompt declined → low min_distance). Stays
    LangChain-agnostic (no agent/ imports).

    Args:
        query: Free-text question (e.g. "do I wash before entering the bath?").
        n_results: Maximum number of KB chunks to retrieve.
        max_distance: Optional cosine-DISTANCE ceiling (Chroma returns distance,
            lower = closer). Chunks with distance > max_distance are dropped.
            When None, no distance filtering is applied.

    Returns:
        ``(records, diagnostics)`` where ``records`` is the SAME list[dict]
        ``query_knowledge`` returns (each: {text, doc_type, source_filename,
        heading, source_ja, source_lang, sources, distance}; missing metadata
        defaults to ""), and ``diagnostics`` is:
          - ``min_distance``: smallest distance among ALL retrieved results
            BEFORE the max_distance filter, or None if nothing was retrieved
            (empty collection) or distances were absent.
          - ``retrieved``: count Chroma returned pre-filter.
          - ``kept``: count surviving the max_distance threshold.
    """
    query = _nonempty_query(query)
    collection = get_kb_collection()
    results = collection.query(
        query_texts=[query],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )
    docs = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    # min_distance is captured over ALL retrieved distances BEFORE the threshold
    # filter, so it remains meaningful even when everything is filtered out (the
    # "did we retrieve anything relevant at all?" signal). None when nothing was
    # retrieved or distances are entirely absent.
    valid_distances = [d for d in distances if d is not None]
    min_distance = min(valid_distances) if valid_distances else None

    records: list[dict] = []
    for doc, meta, distance in zip(docs, metadatas, distances):
        # Drop chunks weaker than the threshold so a weak/empty match falls
        # through to ask-mode's "I don't have that information" path rather than
        # grounding an answer on an irrelevant passage.
        if max_distance is not None and distance is not None and distance > max_distance:
            continue
        meta = meta or {}
        records.append(
            {
                "text": doc,
                "doc_type": meta.get("doc_type", ""),
                "source_filename": meta.get("source_filename", ""),
                "heading": meta.get("heading", ""),
                "source_ja": meta.get("source_ja", ""),
                "source_lang": meta.get("source_lang", ""),
                "sources": meta.get("sources", ""),
                "distance": distance,
            }
        )

    diagnostics = {
        "min_distance": min_distance,
        "retrieved": len(docs),
        "kept": len(records),
    }
    return records, diagnostics


def query_knowledge(
    query: str, n_results: int, max_distance: float | None = None
) -> list[dict]:
    """Semantic search over the Layer 2 KB collection (etiquette, bathing, etc.).

    Sibling of query_onsen_structured, but hits the SEPARATE KB collection
    (get_kb_collection) so onsen and KB never cross-contaminate. Stays
    LangChain-agnostic (no agent/ imports). Drives ask-mode's grounded answer
    and its "I don't know" fallback via the distance threshold.

    Thin wrapper over ``query_knowledge_with_diagnostics`` that drops the
    diagnostics, preserving the original ``list[dict]`` contract for existing
    callers/tests.

    Args:
        query: Free-text question (e.g. "do I wash before entering the bath?").
        n_results: Maximum number of KB chunks to retrieve.
        max_distance: Optional cosine-DISTANCE ceiling (Chroma returns distance,
            lower = closer). Chunks with distance > max_distance are dropped.
            When None, no distance filtering is applied.

    Returns:
        A list of structured chunk records, each shaped:
        {text, doc_type, source_filename, heading, source_ja, source_lang,
        sources, distance}. Metadata may be absent (the KB isn't ingested yet),
        so missing keys default to "". Empty or all-filtered → [].
    """
    records, _ = query_knowledge_with_diagnostics(query, n_results, max_distance)
    return records
