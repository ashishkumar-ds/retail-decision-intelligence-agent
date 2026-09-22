"""Tests for the classifier.dev pre-filter (rag/prefilter.py)."""
from __future__ import annotations

import json

import pytest

import rag.prefilter as prefilter
from rag.corpus import CorpusChunk
from rag.prefilter import filter_retrieved

CHUNKS = [CorpusChunk(chunk_id=f"src:{i}", title=f"t{i}", text=f"chunk {i}",
                      source_type="doc", source_path="doc", license_note="mit",
                      part=1)
          for i in range(4)]
RETRIEVED = [(c, 1.0) for c in CHUNKS]
QUESTION = "why is store 1 declining?"


def _result(label: str, confidence):
    return {"label": label, "confidence": confidence}


def test_confident_not_relevant_dropped(monkeypatch):
    monkeypatch.setattr(prefilter, "_classify_relevance", lambda q, t: [
        _result("relevant", 0.95), _result("not relevant", 0.99),
        _result("relevant", 0.5), _result("not relevant", 0.2),
    ])
    kept = filter_retrieved(QUESTION, RETRIEVED)
    assert [c.chunk_id for c, _ in kept] == ["src:0", "src:2", "src:3"]


def test_mid_confidence_not_relevant_is_kept(monkeypatch):
    """0.7-0.9 confidence is not enough to silently drop context (classifier.dev's
    measured calibration: that band is ~49-85% accurate depending on the task)."""
    monkeypatch.setattr(prefilter, "_classify_relevance", lambda q, t: [
        _result("not relevant", 0.85)] * 4)
    assert len(filter_retrieved(QUESTION, RETRIEVED)) == 4


def test_tier_defaults_to_fast_and_rejects_unknown(monkeypatch):
    bodies = []

    class FakeResponse:
        def read(self):
            return b'{"results": []}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout=None):
        bodies.append(json.loads(request.data.decode()))
        return FakeResponse()
    monkeypatch.setattr(prefilter.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("RAG_PREFILTER_TIER", raising=False)
    prefilter.filter_retrieved(QUESTION, [])
    prefilter._classify_relevance(QUESTION, ["x"])
    monkeypatch.setenv("RAG_PREFILTER_TIER", "ultra")
    prefilter._classify_relevance(QUESTION, ["x"])
    monkeypatch.setenv("RAG_PREFILTER_TIER", "smart")
    prefilter._classify_relevance(QUESTION, ["x"])
    assert [b["tier"] for b in bodies] == ["fast", "fast", "smart"]


def test_null_confidence_keeps_chunk(monkeypatch):
    monkeypatch.setattr(prefilter, "_classify_relevance", lambda q, t: [
        _result("not relevant", None)] * 4)
    kept = filter_retrieved(QUESTION, RETRIEVED)
    assert len(kept) == 4


def test_fail_open_on_api_error(monkeypatch):
    def boom(q, t):
        raise OSError("network down")
    monkeypatch.setattr(prefilter, "_classify_relevance", boom)
    assert filter_retrieved(QUESTION, RETRIEVED) == RETRIEVED


def test_fail_open_on_count_mismatch(monkeypatch):
    monkeypatch.setattr(prefilter, "_classify_relevance",
                        lambda q, t: [_result("relevant", 0.9)])
    assert filter_retrieved(QUESTION, RETRIEVED) == RETRIEVED


def test_env_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("RAG_PREFILTER_ENABLED", "0")
    monkeypatch.setattr(prefilter, "_classify_relevance", boom := (lambda q, t: []))
    assert filter_retrieved(QUESTION, RETRIEVED) == RETRIEVED
    _ = boom  # never called; env gate short-circuits before the API


def test_empty_input_passthrough():
    assert filter_retrieved(QUESTION, []) == []


def test_rephrase_applies_prefilter(monkeypatch):
    calls = {}

    def fake_classify(question, texts):
        calls["question"] = question
        return [_result("not relevant", 0.99), _result("relevant", 0.95),
                _result("not relevant", 0.95), _result("relevant", 0.6)]

    monkeypatch.setattr(prefilter, "_classify_relevance", fake_classify)
    monkeypatch.setenv("LLM_EXPLANATIONS_ENABLED", "1")
    monkeypatch.setattr(prefilter.os, "getenv",
                        lambda k, d=None: "1" if k == "RAG_PREFILTER_ENABLED" else d)

    seen_blocks = {}

    def fake_provider_call(block):
        seen_blocks["block"] = block
        return "ok"

    # stub the provider branch instead of the network
    monkeypatch.setattr(prefilter, "os", prefilter.os)
    import rag.llm_explainer as le
    monkeypatch.setattr(le, "_provider", lambda: "openai_compat")
    monkeypatch.setattr(le, "_rephrase_openai_compat", fake_provider_call)
    narrative, status = le.maybe_llm_narrative(
        store_id=1, question=QUESTION, template_narrative="TEMPLATE",
        evidence={}, corpus=list(CHUNKS), retrieved=RETRIEVED, llm_enabled=True)
    assert narrative == "ok" and status == "llm-grounded"
    assert calls["question"] == QUESTION
    # only the confidently-relevant chunks survived into the evidence block
    assert "[src:src:1]" in seen_blocks["block"]
    assert "[src:src:0]" not in seen_blocks["block"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))