"""Causal guardrail tests (Priority 3): matched-control DiD gating.

Covers:
- assess_causal_evidence states (CONFIRMED / REVIEW_ZONE / REFUTED / UNAVAILABLE);
- the scorer's scale-up gate on MEETS_TARGET evidence;
- the get_control_comparison client's fail-closed envelope validation;
- the app's causal evidence helper (fail-open to INSUFFICIENT, never raises).
"""
import pytest
import requests

from decision_engine.causality import (
    CONFIRMED,
    REFUTED,
    REVIEW_ZONE,
    UNAVAILABLE,
    assess_causal_evidence,
)
from decision_engine.scorer import StoreSignal, score_and_recommend
from tools.forecast_tool import ForecastResponseError, get_control_comparison


def _signal(**overrides):
    base = dict(
        store_id=31642, baseline_forecast=400.0, current_forecast=380.0,
        days_elapsed=30, days_remaining=30, forecast_signal_available=True,
        forecast_status="AVAILABLE",
    )
    base.update(overrides)
    return StoreSignal(**base)


_MEETS_TARGET = {
    "intervention_id": "intervention-x", "evidence_state": "SUFFICIENT",
    "actual_uplift_pct": 9.0, "target_assessment": "MEETS_TARGET",
}


# --- assess_causal_evidence --------------------------------------------------

def test_confirmed_when_did_meets_target():
    out = assess_causal_evidence({"evidence_state": "SUFFICIENT", "did_uplift_pct": 4.0})
    assert out["assessment_state"] == CONFIRMED and out["scale_up_eligible"] is True


def test_review_zone_when_did_positive_but_below_target():
    out = assess_causal_evidence({"evidence_state": "SUFFICIENT", "did_uplift_pct": 1.2})
    assert out["assessment_state"] == REVIEW_ZONE and out["scale_up_eligible"] is False


def test_refuted_when_controls_outperform():
    out = assess_causal_evidence({"evidence_state": "SUFFICIENT", "did_uplift_pct": -9.6})
    assert out["assessment_state"] == REFUTED and out["scale_up_eligible"] is False


@pytest.mark.parametrize("bad", [None, {}, {"evidence_state": "INSUFFICIENT", "did_uplift_pct": 5.0},
                                 {"evidence_state": "SUFFICIENT", "did_uplift_pct": None},
                                 {"evidence_state": "SUFFICIENT", "did_uplift_pct": True}])
def test_missing_or_malformed_evidence_is_unavailable_fail_closed(bad):
    out = assess_causal_evidence(bad)
    assert out["assessment_state"] == UNAVAILABLE and out["scale_up_eligible"] is False


# --- scorer gate -------------------------------------------------------------

def test_scorer_blocks_scale_up_on_refuted_did_and_tempers_confidence():
    base = score_and_recommend(_signal(), outcome_evidence=_MEETS_TARGET)
    rec = score_and_recommend(
        _signal(), outcome_evidence=_MEETS_TARGET,
        causal_evidence={"evidence_state": "SUFFICIENT", "did_uplift_pct": -6.3},
    )
    assert rec["scale_up_eligible"] is False
    assert rec["confidence"] < base["confidence"]
    assert "causal guardrail blocks scale-up" in rec["reason"]


def test_scorer_blocks_scale_up_on_review_zone_did_without_changing_decision():
    base = score_and_recommend(_signal(), outcome_evidence=_MEETS_TARGET)
    rec = score_and_recommend(
        _signal(), outcome_evidence=_MEETS_TARGET,
        causal_evidence={"evidence_state": "SUFFICIENT", "did_uplift_pct": 1.0},
    )
    # Review-zone DiD blocks scale-up but never flips the decision itself.
    assert rec["recommendation"] == base["recommendation"]
    assert rec["confidence"] == base["confidence"]
    assert rec["scale_up_eligible"] is False
    assert "review zone" in rec["reason"].lower()


def test_no_outcome_evidence_stays_backward_compatible():
    rec = score_and_recommend(_signal(), causal_evidence={"evidence_state": "SUFFICIENT", "did_uplift_pct": 5.0})
    assert "scale_up_eligible" not in rec  # gate only exists in the MEETS_TARGET path


# --- get_control_comparison client -------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


def _good_envelope(store_id=317, did=-30.74):
    return {
        "store_id": store_id,
        "windows": {"pre_start": 594, "pre_end": 649, "post_start": 650, "post_end": 710},
        "matched_controls": [{"store_id": 313, "distance": 0.35}],
        "causal": {"did_uplift_pct": did, "treated_change_pct": -8.2, "control_change_pct": 22.5},
        "methodology": {"matching": "k-NN", "effect": "DiD", "caveat": "observational"},
    }


def test_get_control_comparison_validates_envelope(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(_good_envelope()))
    envelope = get_control_comparison(317, 594, 649, 650, 710)
    assert envelope["causal"]["did_uplift_pct"] == -30.74

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"store_id": 317}))
    with pytest.raises(ForecastResponseError):
        get_control_comparison(317, 594, 649, 650, 710)

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(_good_envelope(store_id=999)))
    with pytest.raises(ForecastResponseError):
        get_control_comparison(317, 594, 649, 650, 710)

    bad = _good_envelope()
    bad["matched_controls"] = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(bad))
    with pytest.raises(ForecastResponseError):
        get_control_comparison(317, 594, 649, 650, 710)


def test_get_control_comparison_validates_args():
    with pytest.raises(TypeError):
        get_control_comparison("317", 594, 649, 650, 710)
    with pytest.raises(ValueError):
        get_control_comparison(317, 649, 594, 650, 710)  # pre window inverted
    with pytest.raises(ValueError):
        get_control_comparison(317, 594, 660, 650, 710)  # pre/post overlap


# --- app helper --------------------------------------------------------------

def test_causal_evidence_helper_fails_open_to_insufficient(monkeypatch):
    import app.main as app_main

    def _boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(app_main, "get_control_comparison", _boom)
    out = app_main._causal_evidence_for_intervention(317, 650)
    assert out["evidence_state"] == "INSUFFICIENT"
    assert "unavailable" in out["reason"]

    monkeypatch.setattr(app_main, "get_control_comparison", lambda *a, **k: _good_envelope())
    out = app_main._causal_evidence_for_intervention(317, 650)
    assert out["evidence_state"] == "SUFFICIENT"
    assert out["control_store_ids"] == [313]

