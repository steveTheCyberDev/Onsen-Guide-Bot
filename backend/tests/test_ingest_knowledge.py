"""Unit tests for the KB ingest transforms (scripts/ingest_knowledge.py).

Mirrors test_ingest.py: pure transform coverage (front-matter parsing, chunking,
metadata, deterministic ids, translate path) with the Chroma collection and the
translator mocked — no real DB, network, or embedding calls.
"""

from unittest.mock import MagicMock, patch

import scripts.ingest_knowledge as ik
from scripts.ingest_knowledge import (
    build_metadata,
    chunk_document,
    parse_front_matter,
    split_into_sections,
)

# A representative English doc with front-matter (scalar fields + a sources list)
# and two ## sections, each carrying an inline **Source:** line.
SAMPLE_DOC = """---
doc_type: etiquette
source_lang: en
source_ja: ""
sources:
  - "JNTO — Onsen Etiquette: https://www.japan.travel/en/"
  - "JAL — Top Etiquette Tips: https://www.jal.co.jp/en/"
---

## Before you enter the bath
Onsen are enjoyed fully nude. Wash thoroughly before getting in.

**Source:** JNTO — https://www.japan.travel/en/

## Conduct in the bathing area
Keep your towel out of the water and do not swim or dive.

**Source:** JAL — https://www.jal.co.jp/en/
"""


class TestParseFrontMatter:
    def test_scalar_fields_parsed(self):
        fm, _ = parse_front_matter(SAMPLE_DOC)
        assert fm["doc_type"] == "etiquette"
        assert fm["source_lang"] == "en"
        assert fm["source_ja"] == ""

    def test_sources_parsed_as_list(self):
        fm, _ = parse_front_matter(SAMPLE_DOC)
        assert isinstance(fm["sources"], list)
        assert len(fm["sources"]) == 2
        assert fm["sources"][0].startswith("JNTO")
        assert fm["sources"][1].startswith("JAL")

    def test_body_excludes_front_matter(self):
        _, body = parse_front_matter(SAMPLE_DOC)
        assert body.startswith("## Before you enter the bath")
        assert "doc_type" not in body

    def test_no_front_matter_returns_empty_dict_and_raw_body(self):
        raw = "## Just a heading\nSome text."
        fm, body = parse_front_matter(raw)
        assert fm == {}
        assert body == raw

    def test_unterminated_front_matter_returns_raw(self):
        raw = "---\ndoc_type: etiquette\n\n## Heading\ntext"
        fm, body = parse_front_matter(raw)
        assert fm == {}
        assert body == raw


class TestSplitIntoSections:
    def test_splits_on_heading_and_retains_heading_line(self):
        _, body = parse_front_matter(SAMPLE_DOC)
        sections = split_into_sections(body)
        assert len(sections) == 2
        h0, t0 = sections[0]
        assert h0 == "Before you enter the bath"
        assert t0.startswith("## Before you enter the bath")

    def test_source_lines_retained_in_section(self):
        _, body = parse_front_matter(SAMPLE_DOC)
        sections = split_into_sections(body)
        assert "**Source:**" in sections[0][1]
        assert "**Source:**" in sections[1][1]

    def test_preamble_before_first_heading_not_dropped(self):
        body = "Intro paragraph with no heading.\n\n## A\nSection a."
        sections = split_into_sections(body)
        headings = [h for h, _ in sections]
        assert "" in headings  # preamble kept under an empty heading
        assert "A" in headings


class TestChunkDocument:
    def test_small_section_is_single_chunk(self):
        _, body = parse_front_matter(SAMPLE_DOC)
        chunks = chunk_document(body)
        # Two small sections → exactly two chunks, one per heading.
        assert len(chunks) == 2
        assert chunks[0][0] == "Before you enter the bath"
        assert chunks[1][0] == "Conduct in the bathing area"

    def test_chunks_retain_source_lines(self):
        _, body = parse_front_matter(SAMPLE_DOC)
        chunks = chunk_document(body)
        assert all("**Source:**" in text for _, text in chunks)

    def test_oversized_section_is_split_on_paragraph_boundaries(self):
        # Build one ## section well over the target with several paragraphs.
        para = (
            "This is a fairly long sentence about onsen etiquette that we repeat "
            "to build up some bulk for the chunker to work against here. "
        ) * 3
        para = para.strip()
        body = "## Big section\n" + "\n\n".join(para for _ in range(8))
        chunks = chunk_document(body)
        assert len(chunks) > 1
        # Each chunk stays under the target (with overlap slack), never empty.
        for _, text in chunks:
            assert text.strip()
            assert len(text) <= ik.CHUNK_TARGET_CHARS + ik.CHUNK_OVERLAP_CHARS + len(para)

    def test_no_chunk_splits_mid_sentence(self):
        # Paragraphs are the smallest split unit (split on blank lines), so every
        # complete paragraph appears verbatim somewhere across the chunks — the
        # chunker never cuts inside a sentence.
        para = "Sentence one is here. Sentence two is here. Sentence three is here."
        body = "## H\n" + "\n\n".join(para for _ in range(20))
        chunks = chunk_document(body)
        joined = "\n".join(text for _, text in chunks)
        # The paragraph text survives intact (not chopped mid-sentence).
        assert para in joined
        for _, text in chunks:
            assert text.strip()


class TestBuildMetadata:
    def test_all_required_fields_present(self):
        fm, _ = parse_front_matter(SAMPLE_DOC)
        meta = build_metadata(fm, "etiquette.md", "Conduct in the bathing area", 3)
        assert meta["doc_type"] == "etiquette"
        assert meta["source_filename"] == "etiquette.md"
        assert meta["heading"] == "Conduct in the bathing area"
        assert meta["source_lang"] == "en"
        assert meta["chunk_index"] == 3

    def test_sources_joined_into_a_single_string(self):
        fm, _ = parse_front_matter(SAMPLE_DOC)
        meta = build_metadata(fm, "etiquette.md", "H", 0)
        # Chroma metadata values must be scalars — sources is a joined string.
        assert isinstance(meta["sources"], str)
        assert "JNTO" in meta["sources"] and "JAL" in meta["sources"]

    def test_source_ja_defaults_to_empty_string_when_absent(self):
        # Chroma rejects None — absent source_ja must become "".
        fm = {"doc_type": "about", "source_lang": "en"}  # no source_ja key
        meta = build_metadata(fm, "what_is_onsen.md", "H", 0)
        assert meta["source_ja"] == ""

    def test_missing_sources_becomes_empty_string(self):
        fm = {"doc_type": "about", "source_lang": "en"}
        meta = build_metadata(fm, "what_is_onsen.md", "H", 0)
        assert meta["sources"] == ""

    def test_no_metadata_value_is_none(self):
        # Chroma rejects None for any metadata value.
        fm = {"doc_type": "about", "source_lang": "en"}
        meta = build_metadata(fm, "x.md", "H", 0)
        assert all(v is not None for v in meta.values())


class TestIngestFile:
    """ingest_file end-to-end with the collection + translator mocked."""

    def _write(self, tmp_path, name, content):
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_deterministic_upsert_ids(self, tmp_path):
        md = self._write(tmp_path, "etiquette.md", SAMPLE_DOC)
        fake_collection = MagicMock()
        with patch.object(ik, "get_kb_collection", return_value=fake_collection):
            doc_type, n = ik.ingest_file(md)
        assert doc_type == "etiquette"
        assert n == 2
        _, kwargs = fake_collection.upsert.call_args
        # ids = "<filename>#<chunk_index>" → idempotent re-ingest.
        assert kwargs["ids"] == ["etiquette.md#0", "etiquette.md#1"]

    def test_english_doc_is_pass_through_no_translation(self, tmp_path):
        md = self._write(tmp_path, "etiquette.md", SAMPLE_DOC)
        fake_collection = MagicMock()
        with patch.object(ik, "get_kb_collection", return_value=fake_collection), \
                patch.object(ik, "translate_prose") as fake_translate:
            ik.ingest_file(md)
        # source_lang == "en" → translator never called; English embedded as-is.
        fake_translate.assert_not_called()
        _, kwargs = fake_collection.upsert.call_args
        assert any("fully nude" in doc for doc in kwargs["documents"])

    def test_japanese_doc_translates_before_embedding(self, tmp_path):
        ja_doc = (
            "---\n"
            "doc_type: etiquette\n"
            "source_lang: ja\n"
            'source_ja: "入浴前に体を洗う"\n'
            "sources:\n"
            '  - "Some JA source"\n'
            "---\n\n"
            "## 入浴前\n"
            "入浴前に体をよく洗ってください。\n"
        )
        md = self._write(tmp_path, "etiquette_ja.md", ja_doc)
        fake_collection = MagicMock()
        translated = "## Before bathing\nPlease wash your body thoroughly before bathing."
        with patch.object(ik, "get_kb_collection", return_value=fake_collection), \
                patch.object(ik, "translate_prose", return_value=translated) as fake_translate:
            ik.ingest_file(md)
        # JA→EN translate path exercised; the ENGLISH text is what gets embedded.
        fake_translate.assert_called_once()
        _, kwargs = fake_collection.upsert.call_args
        assert any("wash your body" in doc for doc in kwargs["documents"])
        # Provenance metadata still records the JA source.
        assert kwargs["metadatas"][0]["source_lang"] == "ja"
        assert kwargs["metadatas"][0]["source_ja"] == "入浴前に体を洗う"
