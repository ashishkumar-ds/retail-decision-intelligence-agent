"""Corpus builder for the Tier-2 methodology corpus.

Sources are markdown files under ``rag/sources/`` (seeded with methodology
cards authored in-repo; dunnhumby case-study URLs can be added later via the
same format). Documents are chunked deterministically by heading, then by
paragraph budget, into passages with stable IDs and provenance metadata.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

DEFAULT_SOURCES_DIR = Path(__file__).parent / "sources"
DEFAULT_CORPUS_PATH = Path("logs/rag_corpus.jsonl")

TARGET_CHUNK_CHARS = 900
MIN_CHUNK_CHARS = 120


@dataclass(frozen=True)
class CorpusChunk:
    chunk_id: str
    title: str
    text: str
    source_type: str          # "methodology" | "data_dictionary" | "case_study" | "narrative"
    source_path: str
    license_note: str
    part: int                 # 1-based part within the document
    tier: str = "tier-2"      # grounding tier (2 = supporting external methods)

    def to_record(self) -> dict:
        return asdict(self)


def _chunk_id(title: str, part: int, text: str) -> str:
    digest = hashlib.sha1(f"{title}|{part}|{text}".encode("utf-8")).hexdigest()
    return f"src-{digest[:12]}"


def _split_document(text: str) -> Iterator[tuple[str, str]]:
    """Yield (section_title, section_body) for a markdown document."""
    # Split on markdown headings; keep an implicit intro section.
    sections: list[tuple[str, list[str]]] = [("Introduction", [])]
    for line in text.splitlines():
        if line.startswith("#"):
            heading = line.lstrip("#").strip() or "Untitled"
            sections.append((heading, []))
        else:
            sections[-1][1].append(line)
    for heading, body in sections:
        yield heading, "\n".join(body).strip()


def _chunk_section(doc_title: str, heading: str, body: str, part_start: int) -> list[tuple[int, str]]:
    """Split a section body into paragraph-budget chunks."""
    if not body:
        return []
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in paragraphs:
        if size + len(paragraph) > TARGET_CHUNK_CHARS and current:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(paragraph)
        size += len(paragraph)
    if current:
        chunks.append("\n\n".join(current))
    # Merge tiny trailing fragments back where possible.
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(chunk) < MIN_CHUNK_CHARS:
            merged[-1] = merged[-1] + "\n\n" + chunk
        else:
            merged.append(chunk)
    return [(part_start + i, f"{doc_title} — {heading}\n\n{c}") for i, c in enumerate(merged)]


_FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)\n[ \t]*---[ \t]*\n", re.DOTALL)


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Parse an optional YAML-style front-matter block off a source document.

    Returns ``(metadata, body_without_front_matter)``. A source is ingested
    with whatever metadata its front-matter declares (``title``,
    ``source_type``, ``license``, ``tier``); when absent, the file is
    ingested as a legacy note using folder-inferred metadata. The front
    matter is metadata only — it is never part of the chunked body text.
    """
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip().lower()] = value.strip()
    return meta, text[match.end():]


def _infer_source_type(path: Path) -> str:
    """Folder-based source_type for legacy files with no front matter."""
    if "case_study" in path.parts:
        return "case_study"
    if "data_dictionary" in path.parts or "dictionary" in path.stem:
        return "data_dictionary"
    return "methodology"


def _default_license_note(path: Path) -> str:
    if "case_study" in path.parts:
        return "dunnhumby.com case study (vendor narrative; cited for business context only)"
    return "in-repo methodology note (authored for this project)"


def build_chunks(sources_dir: Path | None = None) -> list[CorpusChunk]:
    """Build the corpus from all markdown sources, deterministically ordered.

    Sources may carry a YAML-style front-matter block (``---...---``) with
    ``title``, ``source_type``, ``license`` and ``tier``. When present it sets
    the chunk metadata and is excluded from the chunked body text; otherwise
    legacy files keep folder-inferred ``source_type``.
    """
    sources_dir = sources_dir or DEFAULT_SOURCES_DIR
    chunks: list[CorpusChunk] = []
    for path in sorted(sources_dir.rglob("*.md")):
        meta, text = _parse_front_matter(path.read_text(encoding="utf-8"))
        doc_title = meta.get("title") or path.stem.replace("_", " ").title()
        source_type = meta.get("source_type") or _infer_source_type(path)
        license_note = meta.get("license") or _default_license_note(path)
        tier = meta.get("tier") or "tier-2"
        part = 1
        for heading, body in _split_document(text):
            emitted = _chunk_section(doc_title, heading, body, part)
            for part_no, chunk_text in emitted:
                chunks.append(CorpusChunk(
                    chunk_id=_chunk_id(doc_title, part_no, chunk_text),
                    title=doc_title,
                    text=chunk_text,
                    source_type=source_type,
                    source_path=str(path.relative_to(sources_dir.parent.parent)),
                    license_note=license_note,
                    part=part_no,
                    tier=tier,
                ))
            part += len(emitted)
    return chunks


def build_corpus(sources_dir: Path | None = None, out_path: Path | None = None) -> list[CorpusChunk]:
    """Build (or rebuild) the corpus JSONL. Deterministic: identical sources
    produce byte-identical files."""
    chunks = build_chunks(sources_dir)
    out_path = out_path or DEFAULT_CORPUS_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk.to_record(), ensure_ascii=False) + "\n")
    return chunks


def load_corpus(path: Path | None = None) -> list[CorpusChunk]:
    """Load corpus chunks from JSONL; missing file -> empty corpus."""
    path = path or DEFAULT_CORPUS_PATH
    if not path.exists():
        return []
    chunks: list[CorpusChunk] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            chunks.append(CorpusChunk(**record))
    return chunks


def get_chunk(chunks: list[CorpusChunk], chunk_id: str) -> CorpusChunk | None:
    return next((c for c in chunks if c.chunk_id == chunk_id), None)
