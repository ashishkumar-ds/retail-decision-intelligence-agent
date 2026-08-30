"""PDF ingestion for the Tier-2 RAG corpus.

Converts a PDF (e.g. the official dunnhumby "The Complete Journey User
Guide") into a deterministic markdown source that the corpus builder can
chunk. Provenance and license notes are embedded in the output header so
every retrieved chunk carries attribution.

Usage:
    python -m rag.ingest_pdf <input.pdf> <output.md> [--title TITLE]
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
            license_note: str) -> str:
    """Full conversion: extracted text -> markdown source with provenance header."""
    body = _apply_sections(extract_pdf_text(pdf_path))
    header = (
        f"# {title}\n\n"
        f"> Source: ingested from `{Path(pdf_path).name}`.\n"
        f"> License: {license_note}\n"
        f"> Ingested into the Tier-2 RAG corpus for methodology/field grounding;\n"
        f"> content is quoted for internal explanation use only.\n\n"
    )
    return header + body + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a PDF into the RAG corpus sources.")
    parser.add_argument("pdf", help="path to the input PDF")
    parser.add_argument("out", help="path to the output markdown source")
    parser.add_argument("--title", default="Ingested PDF Document")
    parser.add_argument("--license", dest="license_note",
                        default="see source PDF for terms")
    args = parser.parse_args()
    markdown = convert(args.pdf, args.title, args.license_note)
    Path(args.out).write_text(markdown, encoding="utf-8")
    print(f"wrote {len(markdown)} chars to {args.out}")


if __name__ == "__main__":
    main()
