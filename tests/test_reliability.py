"""Tests for health/confidence reliability metrics (analytics/reliability.py)."""
from datetime import datetime, timedelta, timezone

import pytest

from analytics.reliability import (
    compute_health_reliability,
    evaluate_outcome_events,
    health_reliability_bins,
    lagged_score_outcome_pairs,
    spearman_p_value,
    spearman_rank_correlation,
)


def _scored(store_id, health, confidence, at):
    return {"store_id": store_id, "store_health_score": health, "confidence": confidence,
            "recommendation": "EXTEND_INTERVENTION",
            "generated_at": at.isoformat()}


def _evaluate(store_id, uplift, at):
    # a full, registry-valid InterventionEvent record: minimal events are
    # silently moved into read-diagnostics and the lag-join would see nothing
    return {"event_id": f"event-{store_id}-{uplift}",
            "intervention_id": f"intervention-{store_id}",
            "event_type": "evaluate",
            "key": {"store_id": store_id, "intervention_type": "recovery",
                    "target_segment": "Best Customers", "campaign_variant": "Campaign 18",
                    "strategy_version": "v1"},
            "payload": {"actual_uplift_pct": uplift, "evidence_state": "SUFFICIENT",
                        "target_assessment": "MEETS_TARGET",
                        "outcome_id": f"outcome-{store_id}"},
            "occurred_at": at.isoformat() if isinstance(at, datetime) else at}


def _outcome_records_from_events():
    return [
        # define/approve/complete events must not enter the lag-join
        {"event_type": "define", "key": {"store_id": 1},
         "payload": {"actual_uplift_pct": 99.0},
         "occurred_at": "2026-01-01T00:00:00+00:00"},
        {"event_type": "evaluate", "key": {"store_id": "not-int"},
         "payload": {"actual_uplift_pct": 3.0},
         "occurred_at": "2026-01-01T00:00:00+00:00"},
        {"event_type": "evaluate", "key": {"store_id": 1},
         "payload": {"actual_uplift_pct": "not-a-number"},
         "occurred_at": "2026-01-01T00:00:00+00:00"},
        {"event_type": "evaluate", "key": {"store_id": 2},
         "payload": {"actual_uplift_pct": 4.0},
         "occurred_at": "2026-01-02T00:00:00+00:00"},
    ]


def test_tie_correction_lowers_perfect_rho():
    # duplicated health scores share average ranks, so a "perfect" pattern is
    # slightly below 1.0 - exactly what tie-correction should do
    rho = spearman_rank_correlation([10, 10, 30, 40], [1, 2, 3, 4])
    assert rho == pytest.approx(0.9487, abs=1e-3)


def test_spearman_directions_and_undefined():
    monotone = spearman_rank_correlation([1, 2, 3, 4], [10, 20, 25, 40])
    assert monotone == pytest.approx(1.0)
    assert spearman_rank_correlation([1, 2, 3, 4], [40, 25, 20, 10]) == pytest.approx(-1.0)
    assert spearman_rank_correlation([1, 2, 3, 4], [5, 5, 5, 5]) is None  # constant ys
    assert spearman_rank_correlation([3, 3, 3], [1, 2, 3]) is None        # constant xs
    assert spearman_rank_correlation([1], [1]) is None                    # n < 2


def test_spearman_p_value_bounds_and_perfect_rho():
    assert spearman_p_value(1.0, 10) == 0.0          # perfect rho: p=0 by construction
    assert spearman_p_value(0.0, 10) == pytest.approx(1.0)
    mid = spearman_p_value(0.6, 40)
    assert 0.0 <= mid < 0.01                          # z = .6*sqrt(39) ~ 3.7
    assert spearman_p_value(0.5, 2) is None           # n < 3 unsupported


def test_lagged_pairing_rejects_same_record_and_below_min_lag():
    t0 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    recommendations = [_scored(1, 80.0, 0.9, t0)]
    outcomes = [
        _evaluate(1, 50.0, t0),                                   # same instant: circular
        _evaluate(1, 20.0, t0 + timedelta(days=10)),               # below min lag
        _evaluate(1, 5.0, t0 + timedelta(days=30)),                 # first valid
        _evaluate(1, 999.0, t0 + timedelta(days=40)),               # later: not the first
    ]
    pairs = lagged_score_outcome_pairs(recommendations, outcomes, min_lag_days=28)
    assert len(pairs) == 1
    assert pairs[0]["actual_uplift_pct"] == 5.0
    assert pairs[0]["lag_days"] == pytest.approx(30.0)
    assert pairs[0]["health"] == 80.0 and pairs[0]["confidence"] == 0.9

    # with the lag off (demo recordings), the same-instant event is STILL
    # rejected (strictly-after is unconditional) - the next event pairs
    no_lag = lagged_score_outcome_pairs(recommendations, outcomes, min_lag_days=0.0)
    assert no_lag[0]["actual_uplift_pct"] == 20.0


def test_lagged_pairing_skips_malformed_rows():
    t0 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    recommendations = [
        _scored(1, 80.0, 0.9, t0),
        {"store_id": 2, "store_health_score": None, "confidence": None,
         "generated_at": "garbage"},
        {"store_id": True, "store_health_score": 50.0, "confidence": 0.5,
         "generated_at": t0.isoformat()},
    ]
    outcomes = [
        {"store_id": 1, "occurred_at": "not-a-date", "actual_uplift_pct": 5.0},
        _evaluate(1, 6.0, t0 + timedelta(days=30)),
    ]
    pairs = lagged_score_outcome_pairs(recommendations, outcomes, min_lag_days=28)
    assert len(pairs) == 1 and pairs[0]["store_id"] == 1 and pairs[0]["actual_uplift_pct"] == 6.0


def test_evaluate_outcome_events_filters_non_measured():
    records = _outcome_records_from_events()
    measured = evaluate_outcome_events(records)
    assert [m["store_id"] for m in measured] == [2]
    assert measured[0]["actual_uplift_pct"] == 4.0


def test_bins_and_directional_hit_rate():
    pairs = [{"health": h, "confidence": 0.9, "actual_uplift_pct": u}
             for h, u in [(10.0, -1.0), (15.0, 2.0), (70.0, 8.0), (70.0, -2.0)]]
    bins = health_reliability_bins(pairs)
    low = next(b for b in bins if b["bin"] == "0-20")
    high = next(b for b in bins if b["bin"] == "60-80")
    assert low["measured_count"] == 2
    assert low["mean_measured_uplift_pct"] == pytest.approx(0.5)
    assert low["positive_share"] == pytest.approx(0.5)
    assert high["measured_count"] == 2
    assert high["mean_measured_uplift_pct"] == pytest.approx(3.0)
    empty = next(b for b in bins if b["bin"] == "20-40")
    assert empty["measured_count"] == 0 and empty["mean_measured_uplift_pct"] is None


def test_compute_verdict_gates_and_methodology_present():
    t0 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    recommendations = [_scored(store, 50.0 + store, 0.8, t0)
                       for store in range(1, 11)]
    outcomes = [_evaluate(store, 1.0 + store, t0 + timedelta(days=40))
                for store in range(1, 11)]
    report = compute_health_reliability(recommendations, outcomes)
    assert report["verdict"] == "directional_only"  # 10 pairs: past the gate, under MEASURED_AT
    assert report["paired_outcome_count"] == 10
    assert report["spearman"]["health_vs_measured_uplift"]["rho"] == pytest.approx(1.0)
    assert "non_circular" in report["methodology"]
    # below the directional gate, the report downgrades itself instead of wowing
    small = compute_health_reliability(recommendations[:4], outcomes[:4])
    assert small["verdict"] == "insufficient_evidence"
    assert small["spearman"]["health_vs_measured_uplift"]["n"] == 4
    # full demo config: no pairs at all
    empty = compute_health_reliability(recommendations, [])
    assert empty["verdict"] == "no_lagged_measured_outcomes"
    assert empty["spearman"]["health_vs_measured_uplift"]["rho"] is None


def test_endpoint_includes_reliability_block(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="FastAPI endpoint tests require FastAPI/Pydantic.")
    import json as jsonlib

    from fastapi.testclient import TestClient

    import app.main as main

    at = "2026-02-01T00:00:00+00:00"
    rec_path = tmp_path / "recommendation.jsonl"
    rec_path.write_text(jsonlib.dumps(
        {"store_id": 1, "recommendation": "CONTINUE", "confidence": 0.9,
         "store_health_score": 75.0, "generated_at": at}) + "\n")
    events_path = tmp_path / "phase2.jsonl"
    events_path.write_text("\n".join(
        jsonlib.dumps(event) for event in [_evaluate(1, 6.0, "2026-03-10T00:00:00+00:00")]) + "\n")
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(rec_path))
    # main binds its registry at import time, so inject the temp-path instance
    from phase2.registry import InterventionRegistry
    monkeypatch.setattr(main, "_phase2_registry",
                        InterventionRegistry(str(events_path)))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "pending.db"))
    monkeypatch.setenv("APPROVAL_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("EXECUTION_JOURNAL_PATH", str(tmp_path / "exec.jsonl"))
    monkeypatch.setenv("RELIABILITY_MIN_LAG_DAYS", "0")
    monkeypatch.setenv(main.APPROVAL_AUTH_TOKEN_ENV, "test-token")
    monkeypatch.setattr(main, "get_store_retail_row", lambda sid: None)
    client = TestClient(main.app)
    response = client.get("/analytics/decision-quality",
                          headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    body = response.json()
    reliability = body["health_reliability"]
    assert reliability["paired_outcome_count"] == 1
    assert reliability["spearman"]["health_vs_measured_uplift"] is not None
    assert body["sufficient_outcome_count"] == 0  # relation between modules unchanged
