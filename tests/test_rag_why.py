"""Grounded "why" RAG tests (Priority 4).

Covers:
- corpus building: deterministic chunking, stable IDs, provenance metadata;
- BM25 retrieval determinism and relevance;
- Tier-1 evidence assembly from recommendation log + registry events;
- narrative synthesis with record-ID citations;
- the numeric grounding guard (fail-closed) and citation validation;
- the /why endpoint end-to-end with TestClient.
"""
import pytest
from fastapi.testclient import TestClient

from rag.corpus import build_chunks, build_corpus, get_chunk
from rag.explainer import (
    build_narrative,
    explain_store,
    gather_store_evidence,
    numeric_grounding_check,
    validate_citations,
)
from rag.retriever import BM25Retriever


# --- Corpus ------------------------------------------------------------------

def test_corpus_is_deterministic_with_stable_ids():
    chunks1 = build_chunks()
    chunks2 = build_chunks()
    assert [c.chunk_id for c in chunks1] == [c.chunk_id for c in chunks2]
    assert len(chunks1) > 10  # four methodology sources should chunk substantially
    for chunk in chunks1:
        assert chunk.chunk_id.startswith("src-")
        assert chunk.source_type in {"methodology", "data_dictionary", "case_study"}
        assert chunk.license_note


def test_corpus_rebuild_is_byte_identical(tmp_path):
    out1, out2 = tmp_path / "c1.jsonl", tmp_path / "c2.jsonl"
    build_corpus(out_path=out1)
    build_corpus(out_path=out2)
    assert out1.read_bytes() == out2.read_bytes()


def test_get_chunk_finds_by_id():
    chunks = build_chunks()
    assert get_chunk(chunks, chunks[0].chunk_id) is not None
    assert get_chunk(chunks, "src-doesnotexist") is None


# --- Retrieval ---------------------------------------------------------------

def test_bm25_ranks_did_document_for_causal_query():
    chunks = build_chunks()
    retriever = BM25Retriever(chunks)
    results = retriever.retrieve("matched controls difference-in-differences market drift", k=3)
    assert results
    assert any("Did Matched Controls" in c.title for c, _ in results)
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True)


def test_bm25_is_deterministic_and_handles_empty_query():
    chunks = build_chunks()
    retriever = BM25Retriever(chunks)
    assert retriever.retrieve("", k=3) == []
    r1 = retriever.retrieve("uplift targeting qini", k=2)
    r2 = retriever.retrieve("uplift targeting qini", k=2)
    assert [(c.chunk_id, s) for c, s in r1] == [(c.chunk_id, s) for c, s in r2]


# --- Tier-1 evidence assembly -------------------------------------------------

def _rec(store_id=317, **overrides):
    base = {
        "store_id": store_id, "recommendation": "CONTINUE", "confidence": 0.95,
        "reason": "on track", "store_health_score": 88.0, "recovery_pct": 9.0,
        "days_remaining": 30, "recommendation_id": "recommendation-abc123",
        "generated_at": "2026-08-30T10:00:00+00:00",
    }
    base.update(overrides)
    return base


def _event(event_id="event-1", event_type="evaluate", store_id=317, payload=None):
    return {
        "event_id": event_id, "intervention_id": "intervention-x",
        "event_type": event_type, "occurred_at": "2026-08-30T12:00:00+00:00",
        "key": {"store_id": store_id}, "payload": payload or {},
    }


def test_gather_store_evidence_pulls_latest_records():
    evidence = gather_store_evidence(
        317,
        recommendation_records=[_rec(), _rec(store_id=999)],
        event_records=[
            _event(payload={"evidence_state": "SUFFICIENT", "actual_uplift_pct": 9.0,
                            "target_assessment": "MEETS_TARGET", "outcome_id": "outcome-1"}),
            _event(event_id="event-0", event_type="start"),
        ],
    )
    assert evidence["latest_recommendation"]["recommendation_id"] == "recommendation-abc123"
    assert evidence["outcome"]["evidence_state"] == "SUFFICIENT"
    assert evidence["outcome"]["actual_uplift_pct"] == 9.0
    assert evidence["intervention_count"] == 1


def test_gather_store_evidence_empty_for_unknown_store():
    evidence = gather_store_evidence(1, recommendation_records=[], event_records=[])
    assert evidence["latest_recommendation"] is None
    assert evidence["outcome"]["evidence_state"] is None


# --- Narrative + grounding guards ---------------------------------------------

def test_narrative_cites_existing_record_ids():
    evidence = gather_store_evidence(317, [_rec()], [_event(payload={})])
    narrative, citations = build_narrative(evidence)
    assert "[rec:recommendation-abc123]" in narrative
    assert any(c["type"] == "rec" for c in citations)
    assert validate_citations(narrative, evidence, build_chunks()) == []


def test_numeric_grounding_guard_passes_on_template_narrative():
    evidence = gather_store_evidence(317, [_rec()], [_event(payload={})])
    narrative, _ = build_narrative(evidence)
    assert numeric_grounding_check(narrative, evidence) == []


def test_numeric_grounding_guard_catches_untraceable_numbers():
    assert numeric_grounding_check(
        "Store 317 improved by 42.7 percent and saved 981 dollars", {"store_id": 317},
    ) == [42.7, 981.0]
    assert numeric_grounding_check(
        "Store 317 improved", {"store_id": 317},
    ) == []  # 317 is traceable to evidence


def test_explain_store_surfaces_methodology_and_guard():
    chunks = build_chunks()
    result = explain_store(
        317, [_rec()], [_event(payload={})], corpus=chunks,
        question="why trust matched-control comparisons?",
    )
    assert result["narrative"].startswith("Store 317")
    assert result["methodology"]
    assert result["guard"] == {"numeric_grounding": "passed", "citations": "passed",
                               "llm": "deterministic-template (no LLM in v1)"}
    for m in result["methodology"]:
        assert get_chunk(chunks, m["chunk_id"]) is not None


def test_explain_store_unknown_store_returns_clean_no_evidence_answer():
    result = explain_store(1, [], [], corpus=build_chunks())
    assert "no recommendation on record" in result["narrative"]
    assert result["methodology"]  # methodology still retrieved for context


# --- Endpoint -------------------------------------------------------------------

def test_why_endpoint_end_to_end(monkeypatch):
    import app.main as app_main

    monkeypatch.setattr(app_main, "read_log", lambda: [_rec()])

    class _FakeRegistry:
        def read_events(self):
            return []

    monkeypatch.setattr(app_main, "_phase2_registry", _FakeRegistry())
    monkeypatch.setattr(app_main, "load_corpus", lambda: build_chunks())

    client = TestClient(app_main.app)
    response = client.get("/why/317")
    assert response.status_code == 200
    body = response.json()
    assert body["narrative"].startswith("Store 317")
    assert body["guard"]["numeric_grounding"] == "passed"
    assert body["citations"]

    response = client.get("/why/999", params={"question": "pricing"})
    assert response.status_code == 200
    assert "no recommendation on record" in response.json()["narrative"]

    assert client.get("/why/317", params={"question": "x" * 501}).status_code == 400

