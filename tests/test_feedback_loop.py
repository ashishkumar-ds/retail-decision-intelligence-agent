"""Outcome feedback loop tests.

Covers the plan -> execute -> measure -> re-decide cycle:
- measured lift from evaluated interventions modulates recommendations;
- negative lift produces PAUSE_INTERVENTION (an approval-gated action);
- actuals ingestion maps dataset days onto the intervention-relative clock;
- inconclusive evidence never changes a decision.
"""
from datetime import datetime, timezone

import httpx
import pytest

from decision_engine.scorer import StoreSignal, score_and_recommend
from tools import forecast_tool


def _signal(**overrides):
    base = dict(
        store_id=31642, baseline_forecast=400.0, current_forecast=380.0,
        days_elapsed=30, days_remaining=30, forecast_signal_available=True,
        forecast_status="AVAILABLE",
    )
    base.update(overrides)
    return StoreSignal(**base)


def _without_timestamp(rec: dict) -> dict:
    # generated_at is wall-clock; decision identity is everything else.
    return {k: v for k, v in rec.items() if k != "generated_at"}


def _baseline_recommendation():
    return _without_timestamp(score_and_recommend(_signal()))


def test_negative_measured_lift_recommends_pause_with_approval():
    rec = score_and_recommend(_signal(), outcome_evidence={
        "intervention_id": "intervention-x", "evidence_state": "SUFFICIENT",
        "actual_uplift_pct": -9.6, "target_assessment": "NEGATIVE",
    })
    assert rec["recommendation"] == "PAUSE_INTERVENTION"
    assert rec["requires_human_approval"] is True
    assert rec["outcome_evidence"]["actual_uplift_pct"] == -9.6
    assert "NEGATIVE" in rec["reason"]


def test_meets_target_raw_lift_alone_is_not_scale_up_eligible():
    # Priority 3 causal guardrail: raw own-baseline lift is NOT causal.
    # Without matched-control DiD confirmation the confidence boost is
    # withheld and scale-up stays blocked (fail-closed).
    base = _baseline_recommendation()
    rec = score_and_recommend(_signal(), outcome_evidence={
        "intervention_id": "intervention-x", "evidence_state": "SUFFICIENT",
        "actual_uplift_pct": 4.2, "target_assessment": "MEETS_TARGET",
    })
    assert rec["recommendation"] == base["recommendation"]
    assert rec["confidence"] == base["confidence"]  # no boost without causal confirmation
    assert rec["scale_up_eligible"] is False
    assert "not causal" in rec["reason"]


def test_meets_target_with_confirmed_did_boosts_confidence():
    rec = score_and_recommend(
        _signal(),
        outcome_evidence={
            "intervention_id": "intervention-x", "evidence_state": "SUFFICIENT",
            "actual_uplift_pct": 4.2, "target_assessment": "MEETS_TARGET",
        },
        causal_evidence={"evidence_state": "SUFFICIENT", "did_uplift_pct": 3.4},
    )
    assert rec["confidence"] >= _baseline_recommendation()["confidence"]
    assert rec["scale_up_eligible"] is True
    assert "scale-up eligible" in rec["reason"]
    assert rec["outcome_evidence"]["causal_evidence"]["assessment_state"] == "CONFIRMED"


def test_review_zone_lift_tempers_confidence_without_flipping_decision():
    base = _baseline_recommendation()
    rec = score_and_recommend(_signal(), outcome_evidence={
        "intervention_id": "intervention-x", "evidence_state": "SUFFICIENT",
        "actual_uplift_pct": 1.1, "target_assessment": "REVIEW_ZONE",
    })
    assert rec["recommendation"] == base["recommendation"]
    assert rec["confidence"] < base["confidence"]
    assert "review zone" in rec["reason"].lower()


def test_inconclusive_evidence_changes_nothing():
    base = _baseline_recommendation()
    for state in ("PARTIAL", "INSUFFICIENT", "NOT_DUE"):
        rec = score_and_recommend(_signal(), outcome_evidence={
            "intervention_id": "intervention-x", "evidence_state": state,
            "actual_uplift_pct": -50.0, "target_assessment": "NEGATIVE",
        })
        # Decision unchanged...
        assert _without_timestamp(rec) == {**base, "outcome_evidence": {
            "intervention_id": "intervention-x", "evidence_state": state,
        }}
        # ...but the inconclusive evidence is still surfaced for provenance,
        # WITHOUT the misleading lift/assessment values (only conclusive
        # evidence carries them).
        assert "actual_uplift_pct" not in rec["outcome_evidence"]


def test_no_evidence_is_backward_compatible():
    assert _without_timestamp(score_and_recommend(_signal())) == _baseline_recommendation()


def _stub_actuals(monkeypatch, observations, envelope_overrides=None):
    def fake_get_actuals(store_id, start_day, end_day, **kwargs):
        return {
            "store_id": store_id, "start_day": start_day, "end_day": end_day,
            "range_start_date": "2017-01-01", "range_end_date": "2018-12-31",
            "observation_count": len(observations),
            "observations": observations,
            **(envelope_overrides or {}),
        }
    monkeypatch.setattr(forecast_tool, "get_actuals", fake_get_actuals)
    # app.main binds get_actuals at import time - patch its reference too.
    import app.main as app_main
    monkeypatch.setattr(app_main, "get_actuals", fake_get_actuals)
    return fake_get_actuals


def test_observations_from_actuals_maps_dataset_days_to_intervention_clock(monkeypatch):
    import app.main as app_main

    started_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    started_day = 600
    _stub_actuals(monkeypatch, [
        {"day": started_day - 56, "date": "2017-11-16", "sales_value": 100.0},
        {"day": started_day + 60, "date": "2018-08-31", "sales_value": 120.0},
    ])
    observations, meta = app_main._observations_from_actuals(31642, started_day, started_at)
    assert meta["observation_count"] == 2
    by_offset = {int((o.observed_at - started_at).days): o for o in observations}
    assert -56 in by_offset and 60 in by_offset
    assert by_offset[-56].value == 100.0
    assert by_offset[-56].source == "actuals_replay"
    assert by_offset[60].value == 120.0


def test_observations_from_actuals_rejects_bad_started_day(monkeypatch):
    import app.main as app_main

    _stub_actuals(monkeypatch, [])
    with pytest.raises(ValueError):
        app_main._observations_from_actuals(31642, "600", datetime.now(timezone.utc))
    with pytest.raises(ValueError):
        app_main._observations_from_actuals(31642, True, datetime.now(timezone.utc))


def test_get_actuals_validates_envelope(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
        def raise_for_status(self):
            pass
        def json(self):
            return self._payload

    good = {
        "store_id": 31642, "start_day": 1, "end_day": 2,
        "range_start_date": "2017-01-01", "range_end_date": "2017-01-02",
        "observation_count": 1,
        "observations": [{"day": 1, "date": "2017-01-01", "sales_value": 99.5}],
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(good))
    envelope = forecast_tool.get_actuals(31642, 1, 2)
    assert envelope["observations"][0]["sales_value"] == 99.5

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse({"store_id": 31642}))
    with pytest.raises(forecast_tool.ForecastResponseError):
        forecast_tool.get_actuals(31642, 1, 2)

    with pytest.raises(ValueError):
        forecast_tool.get_actuals(31642, 5, 1)
    with pytest.raises(TypeError):
        forecast_tool.get_actuals("31642", 1, 2)
