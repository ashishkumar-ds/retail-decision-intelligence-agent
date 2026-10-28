"""Pytest wrapper: the off-path LLM eval suite runs in CI with the unit tests.

Kept separate from ``evaluation/llm_evals.py`` (the operator-facing CLI with the
JSONL audit trail); this module reuses the same runner so CI and local runs can
never diverge. ``tests/conftest.py`` points the telemetry log at a per-test file,
so synthetic drafts never enter the real trail.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from evaluation.llm_cases import CASES, OffpathCase  # noqa: E402
from evaluation.llm_evals import run_case, run_provider_swap  # noqa: E402


@pytest.mark.parametrize("case", CASES, ids=[c.case_id for c in CASES])
def test_offpath_case(case: OffpathCase):
    """Each pinned case: the right gates veto, the right drafts are served."""
    result = run_case(case)
    assert result["passed"], f"{case.case_id}: {result['failures']}"


@pytest.mark.parametrize("case", CASES, ids=[c.case_id for c in CASES])
def test_provider_swap_cannot_serve_ungrounded_content(case: OffpathCase):
    """Holding the harness fixed, a poisoned provider changes nothing a reader sees."""
    swap = run_provider_swap(case)
    assert swap["passed"], f"{case.case_id}: {swap['failures']}"


def test_suite_covers_every_veto_gate():
    """The suite must exercise every gate that can veto, not just the happy path."""
    reasons = {case.expect_reason for case in CASES if case.expect_reason}
    assert reasons == {"numeric", "citation", "lexicon", "empty"}, reasons


def test_suite_proves_the_good_path_is_actually_served():
    """Guards that veto everything would pass every other test here - the suite
    must also pin that grounded drafts DO reach the reader."""
    assert sum(1 for case in CASES if case.expect_served) >= 2


def test_metrics_expose_offpath_llm_series(tmp_path, monkeypatch):
    """The telemetry trail must actually reach /metrics, or it is dead weight."""
    from fastapi.testclient import TestClient

    import app.main as app_main
    from rag import llm_budget
    from rag import llm_telemetry as telemetry

    monkeypatch.setenv("OFFPATH_LLM_LOG_PATH", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("APPROVAL_AUTH_TOKEN", "test-token")
    llm_budget.reset()  # the counters are process-global; pin a known state
    monkeypatch.setenv("LLM_DAILY_CALL_BUDGET", "250")
    telemetry.record("explainer", telemetry.OUTCOME_SERVED, latency_ms=12.5,
                     counts={"tokens_in": 1282, "tokens_out": 139, "cache_read": 1024})
    telemetry.record("explainer", telemetry.OUTCOME_GUARD_REJECTED,
                     reason=telemetry.REASON_NUMERIC)
    telemetry.record("prefilter", telemetry.OUTCOME_SERVED, counts={"kept": 3, "dropped": 1})

    response = TestClient(app_main.app).get(
        "/metrics", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    body = response.text
    assert 'retail_offpath_llm_events_total{layer="explainer",outcome="served"} 1' in body
    assert 'retail_offpath_llm_events_total{layer="explainer",outcome="guard_rejected"} 1' in body
    assert 'retail_offpath_llm_guard_rejections_total{layer="explainer",reason="numeric"} 1' in body
    assert 'retail_offpath_llm_latency_ms_avg{layer="explainer"} 12.5' in body
    # Cost signal: tokens and prompt-cache reads are the only spend the service
    # can see, and the adapters normalise provider-specific field names.
    assert 'retail_offpath_llm_tokens_total{layer="explainer",direction="input"} 1282' in body
    assert 'retail_offpath_llm_tokens_total{layer="explainer",direction="output"} 139' in body
    assert 'retail_offpath_llm_cache_read_tokens_total{layer="explainer"} 1024' in body
    # A prefilter's kept/dropped counts must not masquerade as token spend.
    assert 'retail_offpath_llm_tokens_total{layer="prefilter"' not in body
    assert 'retail_prefilter_chunks_total{result="kept"} 3' in body
    assert 'retail_prefilter_chunks_total{result="dropped"} 1' in body
    # Spend ceiling vs consumption: the fail-closed budget must be visible.
    assert "retail_llm_daily_calls_used 0" in body
    assert "retail_llm_daily_call_budget 250" in body


def test_telemetry_skips_malformed_lines(tmp_path, monkeypatch):
    """Append-only discipline: a corrupt line is skipped and preserved, never fatal."""
    from rag import llm_telemetry as telemetry

    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("OFFPATH_LLM_LOG_PATH", str(path))
    telemetry.record("explainer", telemetry.OUTCOME_SERVED)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")
    telemetry.record("explainer", telemetry.OUTCOME_UNAVAILABLE, detail="boom")

    events = telemetry.read_events()
    assert [event["outcome"] for event in events] == ["served", "unavailable"]
    assert "{not json}" in path.read_text(encoding="utf-8")


def test_telemetry_recording_never_raises(monkeypatch):
    """A broken telemetry path must not be able to break a request."""
    from rag import llm_telemetry as telemetry

    monkeypatch.setenv("OFFPATH_LLM_LOG_PATH", "/proc/definitely/not/writable.jsonl")
    telemetry.record("explainer", telemetry.OUTCOME_SERVED)  # must not raise

