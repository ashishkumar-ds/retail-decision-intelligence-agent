"""Tests for the composed DecisionEngine (TinyAgent-style injection).

The point of the engine is that every pipeline component is injectable: the
defaults are the production components, and tests can stub any stage. These
tests verify both the default pipeline behavior (identical to the pre-engine
evaluate_store) and the injection contract.
"""
from __future__ import annotations

from decision_engine.engine import DecisionEngine
from decision_engine.scorer import StoreSignal


def _healthy() -> StoreSignal:
    return StoreSignal(store_id=1, baseline_forecast=100.0, current_forecast=104.0,
                       days_elapsed=30, days_remaining=30, forecast_signal_available=True)


def _no_signal() -> StoreSignal:
    return StoreSignal(store_id=2, baseline_forecast=0.0, current_forecast=0.0,
                       days_elapsed=30, days_remaining=30, forecast_signal_available=False)


def test_default_pipeline_matches_previous_evaluate_store_behavior():
    rec = DecisionEngine().evaluate(_healthy())
    assert rec["recommendation"] == "CONTINUE"
    assert rec["requires_human_approval"] is False
    assert rec["forecast_status"] == "AVAILABLE"
    steps = [s["step"] for s in rec["trajectory"]["steps"]]
    assert steps == ["route", "plan", "score", "verify", "approval_check"]
    assert rec["trajectory"]["store_id"] == 1


def test_no_data_route_skips_scoring_and_flags_review():
    rec = DecisionEngine().evaluate(_no_signal())
    assert rec["recommendation"] == "NEEDS_REVIEW"
    score_step = next(s for s in rec["trajectory"]["steps"] if s["step"] == "score")
    assert score_step["status"] == "skipped"
    assert rec["requires_human_approval"] is True


def test_components_are_injectable():
    def stub_scorer(signal, **_):
        return {"store_id": signal.store_id, "recommendation": "CONTINUE",
                "confidence": 0.9, "reason": "stubbed", "store_health_score": 80.0,
                "recovery_pct": 1.0, "days_remaining": 5}

    engine = DecisionEngine(scorer=stub_scorer)
    rec = engine.evaluate(_healthy())
    assert rec["reason"] == "stubbed"                      # stub ran
    assert rec["requires_human_approval"] is False         # real verifier/gate still ran
    assert rec["trajectory"]["steps"][-1]["step"] == "approval_check"


def test_injected_verifier_failure_is_recorded_in_trajectory():
    def failing_verifier(_rec):
        return {"passed": False, "details": {"x": False}}

    rec = DecisionEngine(verifier=failing_verifier).evaluate(_healthy())
    verify_step = next(s for s in rec["trajectory"]["steps"] if s["step"] == "verify")
    assert verify_step["status"] == "warning"
