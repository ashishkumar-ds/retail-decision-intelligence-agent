"""Tests for decision-quality metrics (analytics/decision_quality.py)."""
import pytest

from analytics.decision_quality import compute_decision_quality


def _rec(store_id, rec="EXTEND_INTERVENTION", rec_id=None, outcome=None):
    record = {"store_id": store_id, "recommendation": rec,
              "recommendation_id": rec_id or f"rec-{store_id}",
              "requires_human_approval": True}
    if outcome is not None:
        record["outcome_evidence"] = outcome
    return record


def _sufficient(uplift, assessment, causal="CONFIRMED", baseline=100.0):
    return {"evidence_state": "SUFFICIENT", "actual_uplift_pct": uplift,
            "target_assessment": assessment, "baseline_value": baseline,
            "causal_evidence": {"assessment_state": causal}}


def test_acceptance_and_reversal_rates():
    recs = [_rec(1), _rec(2), _rec(3, rec="CONTINUE")]
    decisions = [
        {"recommendation_id": "rec-1", "decision": "approve"},
        {"recommendation_id": "rec-2", "decision": "reject"},
    ]
    executions = [{"event": "execution", "execution_id": "e1", "reversed_at": None},
                  {"event": "execution", "execution_id": "e2", "reversed_at": "t"}]
    quality = compute_decision_quality(recs, decisions, executions)
    assert quality["recommendation_acceptance_rate"] == 0.5
    assert quality["decided_gated_count"] == 2
    assert quality["reversal_rate"] == 0.5


def test_causal_confirmation_and_false_interventions():
    recs = [
        _rec(1, outcome=_sufficient(5.0, "MEETS_TARGET", "CONFIRMED")),
        _rec(2, outcome=_sufficient(1.0, "REVIEW_ZONE", "REVIEW_ZONE")),
        _rec(3, outcome=_sufficient(-2.0, "NEGATIVE", "REFUTED")),
    ]
    quality = compute_decision_quality(recs, [], [])
    assert quality["causal_confirmation_rate"] == pytest_approx(1 / 3)
    assert quality["false_intervention_rate"] == pytest_approx(1 / 3)
    assert quality["sufficient_outcome_count"] == 3


def pytest_approx(value):
    import pytest
    return pytest.approx(value)


def test_margin_and_regret_math():
    # uplift +10% on baseline 100 over 14d at 0.25 -> +35.0; -10% -> -35.0
    recs = [
        _rec(1, outcome=_sufficient(10.0, "MEETS_TARGET", baseline=100.0)),
        _rec(2, outcome=_sufficient(-10.0, "NEGATIVE", baseline=100.0)),
    ]
    quality = compute_decision_quality(recs, [], [], margin_rate=0.25)
    assert quality["incremental_margin_per_intervention"] == pytest_approx(0.0)
    assert quality["total_incremental_margin"] == pytest_approx(0.0)
    assert quality["total_regret_vs_do_nothing"] == pytest_approx(35.0)
    assert quality["stores_with_regret"] == [2]


def test_store_margin_override():
    recs = [_rec(1, outcome=_sufficient(10.0, "MEETS_TARGET", baseline=100.0))]
    quality = compute_decision_quality(recs, [], [], margin_rate=0.25,
                                       store_margins={1: 0.10})
    assert quality["incremental_margin_per_intervention"] == pytest_approx(14.0)


def test_empty_inputs_give_nulls_not_errors():
    quality = compute_decision_quality([], [], [])
    assert quality["recommendation_acceptance_rate"] is None
    assert quality["causal_confirmation_rate"] is None
    assert quality["reversal_rate"] is None
    assert quality["incremental_margin_per_intervention"] is None
    assert quality["total_regret_vs_do_nothing"] == 0.0


def test_decision_quality_endpoint_reads_audit_trail(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="FastAPI endpoint tests require FastAPI/Pydantic.")
    import json

    from fastapi.testclient import TestClient

    import app.main as main

    rec_path = tmp_path / "recommendation.jsonl"
    rec_path.write_text(json.dumps(
        _rec(1, outcome=_sufficient(10.0, "MEETS_TARGET", baseline=100.0))) + "\n")
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(rec_path))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "pending.db"))
    monkeypatch.setenv("APPROVAL_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("EXECUTION_JOURNAL_PATH", str(tmp_path / "exec.jsonl"))
    monkeypatch.setenv(main.APPROVAL_AUTH_TOKEN_ENV, "test-token")
    monkeypatch.setattr(main, "get_store_retail_row", lambda sid: None)
    client = TestClient(main.app)
    response = client.get("/analytics/decision-quality",
                          headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    body = response.json()
    assert body["sufficient_outcome_count"] == 1
    assert body["incremental_margin_per_intervention"] == pytest.approx(35.0)


def test_decision_quality_endpoint_requires_auth(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="FastAPI endpoint tests require FastAPI/Pydantic.")
    from fastapi.testclient import TestClient

    import app.main as main

    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "recommendation.jsonl"))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "pending.db"))
    monkeypatch.delenv(main.APPROVAL_AUTH_TOKEN_ENV, raising=False)
    client = TestClient(main.app)
    assert client.get("/analytics/decision-quality").status_code == 503
