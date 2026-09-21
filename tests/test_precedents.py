"""Tests for precedent-citing advisory evidence (rag/precedents.py)."""
from __future__ import annotations

import pytest

from rag.advisor import maybe_advisory_triage
from rag.llm_explainer import ground_llm_output
from rag.precedents import retrieve_precedents

MINIMAL_EVIDENCE = {"latest_recommendation": {"recommendation": "NEEDS_REVIEW"}}

DECIDED_RECORDS = [
    {
        "store_id": 5, "recommendation_id": "rec-pause-1",
        "recommendation": "PAUSE_INTERVENTION",
        "reason": "Store 5 recovery stalled; pause the campaign.",
        "decided_at": "2026-09-01T00:00:00+00:00",
        "outcome_evidence": {"actual_uplift_pct": -12.5},
    },
    {
        "store_id": 9, "recommendation_id": "rec-cont-1",
        "recommendation": "CONTINUE",
        "reason": "Store 9 healthy; continue monitoring cadence.",
        "decided_at": "2026-09-02T00:00:00+00:00",
    },
    {   # undecided: must never become a precedent
        "store_id": 2, "recommendation_id": "rec-pending",
        "recommendation": "EXTEND_INTERVENTION", "reason": "pending...",
    },
]

QUESTION = "should we pause the store 1 campaign after weak sales?"


def test_decided_records_become_citable_precedents():
    precedents = retrieve_precedents(DECIDED_RECORDS, QUESTION)
    assert precedents
    ids = {c.chunk_id for c in precedents}
    assert "prec-rec-pause-1" in ids or "prec-rec-cont-1" in ids
    assert "prec-rec-pending" not in ids       # undecided records excluded
    for chunk in precedents:
        assert chunk.source_type == "precedent"
        assert chunk.source_path == "recommendation-log"


def test_relevant_precedent_ranks_first():
    precedents = retrieve_precedents(DECIDED_RECORDS, QUESTION, k=2)
    assert precedents[0].chunk_id == "prec-rec-pause-1"  # matches the question
    assert "Measured outcome: -12.5%" in precedents[0].text


def test_measured_precedent_wins_over_plain_decision():
    pause_measured = DECIDED_RECORDS[0]
    pause_plain = {**pause_measured, "recommendation_id": "rec-pause-2", "decided_at": "2026-09-03T00:00:00+00:00"}
    pause_plain.pop("outcome_evidence")
    precedents = retrieve_precedents([pause_plain, pause_measured], QUESTION, k=2)
    assert precedents[0].chunk_id == "prec-rec-pause-1"  # measured beats plain


def test_empty_log_returns_no_precedents():
    assert retrieve_precedents([], QUESTION) == []


def test_precedent_citation_passes_grounding_when_included():
    precedents = retrieve_precedents(DECIDED_RECORDS, QUESTION)
    note = ("Prior intervention on a similar store was paused [src:prec-rec-pause-1]; "
            "the measured outcome argues for caution here.")
    grounded = ground_llm_output(note, MINIMAL_EVIDENCE, list(precedents), [])
    assert "[src:prec-rec-pause-1]" in grounded


def test_precedent_citation_fails_grounding_when_not_included():
    note = "Prior intervention was paused [src:prec-rec-pause-1]."
    with pytest.raises(ValueError):
        ground_llm_output(note, MINIMAL_EVIDENCE, [], [])


def test_precedent_numbers_stay_ungrounded_by_design():
    """Quoting a precedent's measured number must fail: numbers trace only to
    this store's evidence (the asymmetry the module documents)."""
    note = "A prior campaign measured -12.5% lift, so pause this one."
    with pytest.raises(ValueError):
        ground_llm_output(note, MINIMAL_EVIDENCE,
                          retrieve_precedents(DECIDED_RECORDS, QUESTION), [])


def test_advisory_note_may_cite_precedent(monkeypatch):
    import rag.advisor as advisor
    precedents = retrieve_precedents(DECIDED_RECORDS, QUESTION)
    monkeypatch.setattr(advisor, "draft_triage", lambda *a, **k:
                        "SUGGESTED_ACTION: PAUSE_INTERVENTION\n"
                        "NOTE: A prior similar store was paused [src:prec-rec-pause-1] before outcomes confirmed the decline; treat this cautiously.")
    advisory, status = maybe_advisory_triage(
        1, QUESTION, "EXTEND_INTERVENTION", "engine reason", "narrative",
        MINIMAL_EVIDENCE, [], [], llm_enabled=True, precedents=precedents,
    )
    assert advisory["source"] == "llm-advisory"
    assert advisory["suggested_action"] == "PAUSE_INTERVENTION"
    assert "[src:prec-rec-pause-1]" in advisory["note"]
    assert advisory["requires_human_approval"] is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
