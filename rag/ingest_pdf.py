"""PDF ingestion for the Tier-2 RAG corpus.

Converts a PDF (e.g. the official dunnhumby "The Complete Journey User
Guide") into a deterministic markdown source that the corpus builder can
chunk. Provenance and license notes are emitted as YAML front-matter —
the same metadata contract that ``rag.corpus._parse_front_matter`` reads
and that ``scripts/check.py`` enforces — so every retrieved chunk carries
attribution and no ``.md`` output can fail the provenance gate.

The original PDF is NOT the corpus input; it is kept as the provenance
artifact (``rag/sources/pdfs/``). This module extracts its text once into a
committed Markdown source; the corpus build never re-reads the raw PDF.

Usage:
    python -m rag.ingest_pdf <input.pdf> <output.md> [--title TITLE] \\
        [--source-type TYPE] [--tier TIER] [--license NOTE] [--author AUTHOR]
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

# Page footer/footer-page-number artifacts to strip.
_FOOTER_PATTERNS = (
    re.compile(r"^©\s*\d{4}\s*dunnhumby.*?all rights reserved.*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\d{1,3}\s*\|\s*©.*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*©.*all rights reserved.*$", re.IGNORECASE | re.MULTILINE),
)

# Known section markers in the TCJ user guide, in document order, mapped to
# markdown headings. Marker matching is on de-hyphenated, lowercased text.
_TCJ_SECTIONS = (
    ("the complete journey", "# Overview"),
    ("dataset details", "# Dataset Details"),
    ("transaction_data", "## Table: transaction_data"),
    ("hh_demographic", "## Table: hh_demographic"),
    ("campaign_table", "## Table: campaign_table"),
    ("campaign_desc", "## Table: campaign_desc"),
    ("product", "## Table: product"),
    ("coupon_redempt", "## Table: coupon_redempt"),
    ("coupon", "## Table: coupon"),
    ("causal_data", "## Table: causal_data"),
    ("case study", "# Case Study: Household 208"),
    ("contact information", "# Contact"),
)


def extract_pdf_text(pdf_path: str | Path) -> str:
    """Extract text from all pages, de-hyphenating line-broken words."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    raw = "\n".join((page.extract_text() or "") for page in reader.pages)
    # Rejoin words broken across lines by bullets/headings ("O\nf those" -> "Of those").
    text = re.sub(r"\b(\w)\n(\w)", r"\1\2", raw)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # hyphenated line breaks
    for pattern in _FOOTER_PATTERNS:
        text = pattern.sub("", text)
    # Collapse whitespace runs but keep paragraph breaks.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _apply_sections(text: str) -> str:
    """Insert markdown headings at known section markers (TCJ guide layout)."""
    lower = text.lower()
    cuts: list[tuple[int, str]] = []
    for marker, heading in _TCJ_SECTIONS:
        # Find the marker that is not already part of a heading insertion.
        position = lower.find(marker)
        if position == -1:
            continue
        cuts.append((position, heading))
    cuts.sort()
    out = text
    for position, heading in reversed(cuts):
        line_start = out.rfind("\n", 0, position) + 1
        if out[line_start:position].strip() == "" and out[position:position + 1] != "#":
            out = out[:line_start] + f"\n{heading}\n" + out[position:]
    return out


def convert(pdf_path: str | Path, title: str,
            license_note: str, *, source_type: str = "data_dictionary",
            tier: str = "tier-2", author: str | None = None) -> str:
    """Full conversion: extracted text -> markdown source with YAML front-matter.

    The front-matter matches what ``rag.corpus._parse_front_matter`` ingests
    and what ``scripts/check.py`` requires (``source_type``/``license``/``tier``),
    so the output drops straight into ``rag/sources/**`` and passes the gate.
    The original PDF remains the provenance artifact; it is referenced by
    ``url:``, not re-parsed at corpus build time.
    """
    body = _apply_sections(extract_pdf_text(pdf_path))
    author_line = f"author: {author}\n" if author else ""
    header = (
        "---\n"
        f"source_type: {source_type}\n"
        f"title: {title}\n"
        f"url: in-repo copy of {Path(pdf_path).name}\n"
        f"{author_line}"
        f"license: {license_note}\n"
        f"tier: {tier}\n"
        "---\n\n"
    )
    return header + body + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a PDF into the RAG corpus sources.")
    parser.add_argument("pdf", help="path to the input PDF")
    parser.add_argument("out", help="path to the output markdown source")
    parser.add_argument("--title", default="Ingested PDF Document")
    parser.add_argument("--license", dest="license_note",
                        default="see source PDF for terms")
    parser.add_argument("--source-type", default="data_dictionary",
                        choices=["methodology", "data_dictionary", "case_study", "narrative"])
    parser.add_argument("--tier", default="tier-2")
    parser.add_argument("--author", default=None)
    args = parser.parse_args()
    markdown = convert(args.pdf, args.title, args.license_note,
                       source_type=args.source_type, tier=args.tier,
                       author=args.author)
    Path(args.out).write_text(markdown, encoding="utf-8")
    print(f"wrote {len(markdown)} chars to {args.out}")


if __name__ == "__main__":
    main()
