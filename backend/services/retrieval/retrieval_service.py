from vectorstore.store import get_collection


def query_onsen(query: str, n_results: int = 5) -> str:
    collection = get_collection()
    results = collection.query(query_texts=[query], n_results=n_results)
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
