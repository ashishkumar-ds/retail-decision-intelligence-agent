"""Precedent evidence for the advisory layer (rag/precedents.py).

The advisory is the only LLM surface that may reason about what happened
*before* — so it gets the system's own decision history as evidence. Decided
records (the append-only recommendation log is the system of record) become
retrievable precedent chunks, ranked with the SAME hand-rolled BM25 the
methodology corpus uses — no new retrieval code, no new dependency.

Discipline:

- Precedents are *citable* (``[src:prec-<recommendation_id>]``) but their
  numbers are NOT grounding-valid: a note that quotes a precedent's measured
  uplift still fails the numeric grounding check, because that check traces
  numbers to the store's own evidence only. Precedents carry qualitative
  history; measured claims stay anchored to this store's records. That
  asymmetry is deliberate (see rag/llm_explainer.py).
- Nothing here mutates state. Read-only over the log.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .corpus import CorpusChunk
from .retriever import BM25Retriever

PRECEDENT_SOURCE = "recommendation-log"


def _record_chunk(record: Mapping[str, Any]) -> CorpusChunk | None:
    """One decided recommendation -> one precedent chunk (or None)."""
    if not record.get("decided_at") or not record.get("recommendation_id"):
        return None
    recommendation = record.get("recommendation")
    reason = record.get("reason")
    if not recommendation or not reason:
        return None
    outcome = record.get("outcome_evidence") or {}
    lift = outcome.get("actual_uplift_pct")
    measured = (f" Measured outcome: {lift:+.1f}% observed sales lift."
                if isinstance(lift, (int, float)) and not isinstance(lift, bool) else "")
    return CorpusChunk(
        chunk_id=f"prec-{record['recommendation_id']}",
        title=f"Prior decision: store {record.get('store_id')} -> {recommendation}",
        text=f"{reason}{measured}",
        source_type="precedent",
        source_path=PRECEDENT_SOURCE,
        license_note="in-repo",
        part=1,
    )


def retrieve_precedents(records: Sequence[Mapping[str, Any]], query: str,
                        k: int = 2) -> list[CorpusChunk]:
    """Rank decided records by BM25 relevance to the reviewer's question.

    Returns at most ``k`` chunks, most relevant first. Records without a
    decision or reason are skipped; records with measured outcome evidence are
    preferred over plain decisions (they answer "what happened").
    """
    chunks = [chunk for chunk in (_record_chunk(r) for r in records) if chunk is not None]
    if not chunks:
        return []
    measured = {c.chunk_id: c for c in chunks if "Measured outcome" in c.text}
    ranked = BM25Retriever(chunks).retrieve(query or "prior decision", k=len(chunks))
    # Prefer measured precedents among equally relevant hits: sort by
    # (has-measurement) before truncating to k.
    ranked.sort(key=lambda pair: (pair[0].chunk_id not in measured, -pair[1]))
    return [chunk for chunk, _score in ranked[:k]]
