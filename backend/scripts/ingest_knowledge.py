"""Ingest the Layer 2 knowledge-base markdown docs into ChromaDB.

Usage (from the backend/ dir):
    python -m scripts.ingest_knowledge

Reads every ``*.md`` under ``settings.kb_data_dir`` (the sibling-of-onsen-data
knowledge dir; env-overridable via KB_DATA_PATH), parses the YAML-ish front-matter
(``doc_type``, ``source_lang``, ``source_ja``, ``sources:`` list), translates the
prose JA→EN at ingest when ``source_lang != "en"`` (a no-op pass-through for the
current English docs), chunks each doc by ``##`` heading with a size cap, and
upserts the English chunks into the KB collection (``settings.kb_collection``).

This is a SEPARATE script from ``scripts/ingest.py`` on purpose: that one is tied
to onsen JSONL shape (parse_location, SPA_QUALITY_MAP, detail_url ids, sibling
write-back). Prose markdown has none of that, so each ingester stays
single-purpose. It reuses ``get_kb_collection()`` so the ingest job writes to the
SAME ChromaDB path the app reads from (settings.chroma_path) and the SAME
text-embedding-3-small embedding function.

Deterministic chunk ids (``"<filename>#<chunk_index>"``) make re-ingest idempotent
(upsert), like the onsen path.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv

load_dotenv(BACKEND_DIR / ".env")

import openai

from core.config import settings
from vectorstore.store import get_kb_collection

# ── Chunking parameters ───────────────────────────────────────────────────────
# Prose chunking targets: one ## heading section = one chunk when it fits, else
# split on paragraph boundaries (never mid-sentence). Tuned small while the KB is
# tiny; graduate via the ask eval as it grows.
CHUNK_TARGET_CHARS = 800  # soft upper bound — a section larger than this is split
CHUNK_MIN_CHARS = 500  # try to fill a chunk to at least this before splitting
CHUNK_OVERLAP_CHARS = 100  # carry ~this many trailing chars into the next chunk

# ── Translation ───────────────────────────────────────────────────────────────

client = openai.OpenAI(api_key=settings.openai_api_key)


def translate_prose(text: str) -> str:
    """Translate a block of Japanese prose to English (gpt-4o-mini, temp 0).

    Mirrors the translate-at-ingest pattern in scripts/ingest.py:translate_batch
    (gpt-4o-mini, temperature 0, JSON in/out for a robust round-trip). Only called
    for docs whose front-matter ``source_lang != "en"``; English docs skip this
    entirely (pass-through). We always embed the English text.
    """
    payload = json.dumps({"text": text}, ensure_ascii=False)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You translate Japanese onsen knowledge-base prose into natural, "
                    "fluent English. Preserve markdown structure, '## ' headings, and "
                    "any inline '**Source:**' citation lines verbatim. Return a JSON "
                    'object {"text": "<english markdown>"}. Output only valid JSON, no '
                    "markdown fences."
                ),
            },
            {"role": "user", "content": payload},
        ],
        temperature=0,
    )
    return json.loads(response.choices[0].message.content)["text"]


# ── Front-matter parsing ──────────────────────────────────────────────────────
# PyYAML is NOT a project dependency (checked requirements.txt), and the
# front-matter format is a simple, fixed subset: '---'-delimited, scalar key:value
# lines plus exactly one block list (``sources:`` followed by ``  - "..."`` items).
# A tiny hand-rolled parser keeps the dependency surface unchanged.


def parse_front_matter(raw: str) -> tuple[dict, str]:
    """Split a markdown doc into (front_matter_dict, body).

    Expects the doc to start with a ``---`` line, a block of ``key: value`` and
    ``key:`` + ``  - item`` list entries, then a closing ``---`` line. Scalars are
    unquoted/stripped; list values become a Python ``list[str]``. If there is no
    leading front-matter block, returns ``({}, raw)``.
    """
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw

    # Find the closing fence.
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, raw

    fm: dict = {}
    current_list_key: str | None = None
    for line in lines[1:end]:
        if not line.strip():
            continue
        # A list item under the most recent ``key:`` (e.g. ``  - "url"``).
        stripped = line.strip()
        if stripped.startswith("- ") and current_list_key is not None:
            item = stripped[2:].strip().strip('"').strip("'")
            fm[current_list_key].append(item)
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "":
                # Opens a block list (its items follow as ``- ...`` lines).
                fm[key] = []
                current_list_key = key
            else:
                fm[key] = value.strip('"').strip("'")
                current_list_key = None
    body = "\n".join(lines[end + 1 :]).strip()
    return fm, body


# ── Chunking ──────────────────────────────────────────────────────────────────


def split_into_sections(body: str) -> list[tuple[str, str]]:
    """Split a doc body into (heading, section_text) pairs on ``## `` headings.

    Each returned ``section_text`` INCLUDES the heading line itself and any inline
    ``**Source:**`` citation lines (provenance travels with the chunk). Content
    before the first ``## `` heading (rare — a preamble) is returned under an empty
    heading so it is never silently dropped.
    """
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []

    def flush() -> None:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append((current_heading, text))

    for line in body.splitlines():
        if line.startswith("## "):
            flush()
            current_heading = line[3:].strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    flush()
    return sections


def _split_oversized(section_text: str) -> list[str]:
    """Split one oversized section into chunks on paragraph (blank-line) boundaries.

    Never splits mid-sentence — paragraphs are the smallest unit. Packs paragraphs
    up toward CHUNK_TARGET_CHARS, then starts a new chunk carrying ~CHUNK_OVERLAP_CHARS
    of trailing text from the previous chunk for retrieval continuity.
    """
    paragraphs = [p.strip() for p in section_text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        candidate = f"{buf}\n\n{para}" if buf else para
        if buf and len(candidate) > CHUNK_TARGET_CHARS:
            chunks.append(buf)
            overlap = buf[-CHUNK_OVERLAP_CHARS:] if CHUNK_OVERLAP_CHARS else ""
            buf = f"{overlap}\n\n{para}" if overlap else para
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks


def chunk_document(body: str) -> list[tuple[str, str]]:
    """Chunk a doc body into (heading, chunk_text) pairs.

    One ``## `` section becomes one chunk when it fits under CHUNK_TARGET_CHARS;
    larger sections are split on paragraph boundaries (``_split_oversized``). Small
    sections stay a single chunk. The heading line and inline ``**Source:**`` lines
    are retained inside every chunk.
    """
    chunks: list[tuple[str, str]] = []
    for heading, section_text in split_into_sections(body):
        if len(section_text) <= CHUNK_TARGET_CHARS:
            chunks.append((heading, section_text))
        else:
            for piece in _split_oversized(section_text):
                chunks.append((heading, piece))
    return chunks


# ── Metadata ──────────────────────────────────────────────────────────────────


def build_metadata(
    front_matter: dict,
    filename: str,
    heading: str,
    chunk_index: int,
) -> dict:
    """Metadata dict for one KB chunk's Chroma upsert.

    ChromaDB rejects None values and non-scalar values, so:
    - ``source_ja`` is stored as "" when absent (mirrors build_metadata in
      scripts/ingest.py).
    - the front-matter ``sources:`` list is joined into a single newline-delimited
      string (Chroma metadata must be scalars, not lists). The empty list → "".
    """
    sources = front_matter.get("sources", []) or []
    return {
        "doc_type": front_matter.get("doc_type", "") or "",
        "source_filename": filename,
        "heading": heading,
        "source_lang": front_matter.get("source_lang", "") or "",
        "source_ja": front_matter.get("source_ja", "") or "",
        "chunk_index": chunk_index,
        "sources": "\n".join(sources),
    }


# ── Main ──────────────────────────────────────────────────────────────────────


def ingest_file(md_path: Path) -> tuple[str, int]:
    """Ingest one markdown doc; return (doc_type, chunks_upserted)."""
    raw = md_path.read_text(encoding="utf-8")
    front_matter, body = parse_front_matter(raw)
    filename = md_path.name

    # Translate-at-ingest: only when the source is not English. We always embed
    # the English text. Current docs are all source_lang="en" → pass-through.
    if front_matter.get("source_lang", "en") != "en":
        print(f"  Translating {filename} (source_lang={front_matter.get('source_lang')}) JA→EN...")
        body = translate_prose(body)

    chunks = chunk_document(body)
    collection = get_kb_collection()

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    for chunk_index, (heading, chunk_text) in enumerate(chunks):
        ids.append(f"{filename}#{chunk_index}")
        documents.append(chunk_text)
        metadatas.append(build_metadata(front_matter, filename, heading, chunk_index))

    if ids:
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

    doc_type = front_matter.get("doc_type", "") or "unknown"
    print(f"  {filename}: doc_type={doc_type}, {len(ids)} chunks upserted.")
    return doc_type, len(ids)


def ingest(kb_dir: Path) -> None:
    """Ingest every ``*.md`` under ``kb_dir`` into the KB collection."""
    md_files = sorted(kb_dir.glob("*.md"))
    print(f"Found {len(md_files)} markdown docs under {kb_dir}")
    if not md_files:
        print("Nothing to ingest.")
        return

    total_chunks = 0
    per_doc_type: Counter = Counter()
    for md_path in md_files:
        doc_type, n = ingest_file(md_path)
        total_chunks += n
        per_doc_type[doc_type] += n

    print(f"\nDone. {len(md_files)} files read, {total_chunks} chunks upserted.")
    print("Chunks per doc_type:")
    for doc_type, n in sorted(per_doc_type.items()):
        print(f"  {doc_type}: {n}")
    print(f"Total in KB collection: {get_kb_collection().count()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest the Layer 2 knowledge-base markdown docs into ChromaDB"
    )
    parser.add_argument(
        "--dir",
        default=None,
        help="Override the KB markdown directory (default: settings.kb_data_dir).",
    )
    args = parser.parse_args()

    kb_dir = Path(args.dir) if args.dir else settings.kb_data_dir
    ingest(kb_dir)
