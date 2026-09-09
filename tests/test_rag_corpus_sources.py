"""Tests for RAG corpus source attribution and metadata.

Covers the merchant-agent/P4 ingestion hygiene:
- front-matter metadata is parsed and stripped from chunked body text
- every ingested source carries source_type/license/tier metadata
- data_dictionary/methodology/narrative are distinguished (regression for the
  bug that collapsed all sources into `methodology`)
- corpus build stays deterministic with the new sources
"""
from __future__ import annotations

from rag.corpus import (
    DEFAULT_SOURCES_DIR,
    CorpusChunk,
    _parse_front_matter,
    build_chunks,
)


def _source_files() -> list:
    return sorted(DEFAULT_SOURCES_DIR.rglob("*.md"))


def test_parse_front_matter_extracts_metadata_and_body():
    meta, body = _parse_front_matter(
        "---\nsource_type: methodology\ntitle: A\nlicense: x\ntier: tier-2\n---\n\n# Heading\nbody"
    )
    assert meta["source_type"] == "methodology"
    assert meta["tier"] == "tier-2"
    assert "# Heading" in body
    assert "---" not in body


def test_parse_front_matter_returns_empty_for_plain_doc():
    meta, body = _parse_front_matter("# No front matter\nplain")
    assert meta == {}
    assert body == "# No front matter\nplain"


def test_every_source_file_carries_required_metadata():
    missing = []
    for path in _source_files():
        meta, _ = _parse_front_matter(path.read_text(encoding="utf-8"))
        required = ("source_type", "license", "tier")
        lacks = [k for k in required if not meta.get(k)]
        if lacks:
            missing.append((path.name, lacks))
    assert missing == []


def test_corpus_distinguishes_source_types():
    types = {c.source_type for c in build_chunks()}
    assert "methodology" in types
    assert "data_dictionary" in types
    assert "narrative" in types


def test_data_dictionary_folder_files_are_tagged_data_dictionary():
    # Regression: previously every source collapsed to "methodology" because
    # folder detection checked path.stem, not path.parts.
    dict_chunks = [c for c in build_chunks() if "data_dictionary" in c.source_path]
    assert dict_chunks
    assert all(c.source_type == "data_dictionary" for c in dict_chunks)


def test_chunk_carries_tier_default():
    chunk = build_chunks()[0]
    assert isinstance(chunk, CorpusChunk)
    assert chunk.tier == "tier-2"


def test_corpus_build_is_deterministic_with_new_sources():
    a = [c.to_record() for c in build_chunks()]
    b = [c.to_record() for c in build_chunks()]
    assert a == b


def test_ingest_pdf_emits_front_matter_that_passes_the_gate(monkeypatch):
    """PDF->Markdown output must satisfy the corpus provenance contract.

    Standard practice: the PDF is provenance; the corpus reads the extracted
    Markdown. So ingest_pdf's output must carry source_type/license/tier
    front-matter that corpus._parse_front_matter accepts and check.py requires.
    """
    import rag.ingest_pdf as ingest
    monkeypatch.setattr(ingest, "extract_pdf_text", lambda p: "# Overview\n\nplain body text")
    out = ingest.convert("user_guide.pdf", "The Guide", "see source",
                         source_type="data_dictionary", author="dunnhumby")
    meta, body = _parse_front_matter(out)
    assert all(meta.get(k) for k in ("source_type", "license", "tier"))
    assert meta["source_type"] == "data_dictionary"
    assert meta["url"].endswith("user_guide.pdf")
    assert "# Overview" in body
    assert "source_type:" in out.split("---", 2)[1]  # in the front-matter block


def test_krzykov_narrative_is_marked_narrative_not_authority():
    narrative = [c for c in build_chunks() if c.source_type == "narrative"]
    assert narrative
    # narrative chunks carry a non-authoritative disclaimer
    assert any("not a methods authority" in c.text.lower() for c in narrative)