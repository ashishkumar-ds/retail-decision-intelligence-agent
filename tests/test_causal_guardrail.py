"""Causal guardrail tests (Priority 3): matched-control DiD gating.

Covers:
- assess_causal_evidence states (CONFIRMED / REVIEW_ZONE / REFUTED / UNAVAILABLE);
- the scorer's scale-up gate on MEETS_TARGET evidence;
- the get_control_comparison client's fail-closed envelope validation;
- the app's causal evidence helper (fail-open to INSUFFICIENT, never raises).
"""
import httpx
import pytest

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


def test_confirmed_downgraded_when_ci_admits_no_effect():
    """P2: point +4% clears the bar, but CI [-0.5, 6.2] admits no effect.

    The Part 1 calibration estimate itself (+2.84%, CI [-0.5, 6.2]) lands
    REVIEW_ZONE under this rule - directionally positive, not proven.
    """
    out = assess_causal_evidence({"evidence_state": "SUFFICIENT", "did_uplift_pct": 4.0,
                                  "did_ci95": [-0.5, 6.2]})
    assert out["assessment_state"] == REVIEW_ZONE and out["scale_up_eligible"] is False
    assert "downgrade_reason" in out


def test_confirmed_stands_when_ci_clears_zero():
    out = assess_causal_evidence({"evidence_state": "SUFFICIENT", "did_uplift_pct": 4.0,
                                  "did_ci95": [0.5, 7.5]})
    assert out["assessment_state"] == CONFIRMED and out["scale_up_eligible"] is True


def test_confirmed_downgraded_when_below_mde():
    out = assess_causal_evidence({"evidence_state": "SUFFICIENT", "did_uplift_pct": 4.0,
                                  "mde_pct": 5.0})
    assert out["assessment_state"] == REVIEW_ZONE and out["scale_up_eligible"] is False


def test_malformed_diagnostics_do_not_move_the_point_rule():
    """Missing/malformed CI or MDE never promote or demote on their own."""
    for causal in ({"evidence_state": "SUFFICIENT", "did_uplift_pct": 4.0, "did_ci95": None},
                   {"evidence_state": "SUFFICIENT", "did_uplift_pct": 4.0, "did_ci95": ["x", "y"]},
                   {"evidence_state": "SUFFICIENT", "did_uplift_pct": 4.0, "mde_pct": True}):
        out = assess_causal_evidence(causal)
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
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(_good_envelope()))
    envelope = get_control_comparison(317, 594, 649, 650, 710)
    assert envelope["causal"]["did_uplift_pct"] == -30.74

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse({"store_id": 317}))
    with pytest.raises(ForecastResponseError):
        get_control_comparison(317, 594, 649, 650, 710)

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(_good_envelope(store_id=999)))
    with pytest.raises(ForecastResponseError):
        get_control_comparison(317, 594, 649, 650, 710)

    bad = _good_envelope()
    bad["matched_controls"] = []
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(bad))
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
        raise httpx.ConnectError("down")

    monkeypatch.setattr(app_main, "get_control_comparison", _boom)
    out = app_main._causal_evidence_for_intervention(317, 650)
    assert out["evidence_state"] == "INSUFFICIENT"
    assert "unavailable" in out["reason"]

    monkeypatch.setattr(app_main, "get_control_comparison", lambda *a, **k: _good_envelope())
    out = app_main._causal_evidence_for_intervention(317, 650)
    assert out["evidence_state"] == "SUFFICIENT"
    assert out["control_store_ids"] == [313]



def test_get_control_comparison_sends_pool_and_validates_it(monkeypatch):
    seen = {}

    class _Capture(_FakeResponse):
        pass

    def _capture(method, url, **kwargs):
        seen.update(kwargs.get("params", {}))
        return _FakeResponse(_good_envelope())

    import tools.forecast_tool as forecast_tool

    monkeypatch.setattr(forecast_tool, "_request_with_retry",
                        lambda method, url, **kwargs: _capture(method, url, **kwargs))
    get_control_comparison(317, 594, 649, 650, 710)
    assert seen.get("pool") == "all"  # default preserves prior behavior
    get_control_comparison(317, 594, 649, 650, 710, pool="underperforming")
    assert seen.get("pool") == "underperforming"
    with pytest.raises(ValueError):
        get_control_comparison(317, 594, 649, 650, 710, pool="everything")


def test_causal_evidence_helper_carries_diagnostics(monkeypatch):
    import app.main as app_main

    envelope = _good_envelope()
    envelope["pool"] = "underperforming"
    envelope["causal"].update({
        "did_ci95": [-32.0, -29.0], "mde_pct": 1.5,
        "placebo_did_pct": 0.3, "pre_trend_divergence": 0.8,
    })
    monkeypatch.setattr(app_main, "get_control_comparison", lambda *a, **k: envelope)
    out = app_main._causal_evidence_for_intervention(317, 650)
    assert out["evidence_state"] == "SUFFICIENT"
    assert out["did_ci95"] == [-32.0, -29.0]
    assert out["mde_pct"] == 1.5
    assert out["placebo_did_pct"] == 0.3
    assert out["pre_trend_divergence"] == 0.8
    assert out["control_pool"] == "underperforming"
