"""Diversified recommendation policy + budget allocator tests (Priority 2).

Covers:
- new diversified action types (RETARGET_SEGMENT / TIMING_SHIFT /
  REALLOCATE_BUDGET) fire only on proven (MEETS_TARGET) evidence + context;
- every new action type is approval-gated via guardrails;
- the deterministic budget allocator: eligibility, cap, rounding, errors;
- backward compatibility: no context -> decision unchanged.
"""
import pytest

from decision_engine.scorer import StoreSignal, score_and_recommend
from guardrails import requires_human_approval
from phase2.budget_allocator import (
    MAX_STORE_SHARE,
    MIN_ALLOCATION,
    BudgetAllocatorError,
    allocate_budget,
)


def _signal(**overrides):
    base = dict(
        store_id=31642, baseline_forecast=400.0, current_forecast=380.0,
        days_elapsed=30, days_remaining=30, forecast_signal_available=True,
        forecast_status="AVAILABLE",
    )
    base.update(overrides)
    return StoreSignal(**base)


_MEETS_TARGET_EVIDENCE = {
    "intervention_id": "intervention-x", "evidence_state": "SUFFICIENT",
    "actual_uplift_pct": 9.0, "target_assessment": "MEETS_TARGET",
}


# --- Diversified recommendation policy --------------------------------------

def test_retarget_segment_fires_on_proven_evidence_with_segment_context():
    rec = score_and_recommend(
        _signal(), outcome_evidence=_MEETS_TARGET_EVIDENCE,
        store_context={"non_best_customer_household_share": 0.72},
    )
    assert rec["recommendation"] == "RETARGET_SEGMENT"
    assert rec["requires_human_approval"] is True
    assert rec["diversification"]["non_best_customer_household_share"] == 0.72
    assert "Best Customer" in rec["reason"]


def test_timing_shift_fires_on_peak_concentration_context():
    rec = score_and_recommend(
        _signal(), outcome_evidence=_MEETS_TARGET_EVIDENCE,
        store_context={"peak_hour_sales_concentration": 0.55, "peak_hour_window": "12 PM - 6 PM"},
    )
    assert rec["recommendation"] == "TIMING_SHIFT"
    assert rec["requires_human_approval"] is True
    assert rec["diversification"]["peak_hour_window"] == "12 PM - 6 PM"


def test_reallocate_budget_fires_when_constrained_and_confident():
    # health 100 -> boundary confidence 1.0 >= 0.7 threshold
    rec = score_and_recommend(
        _signal(current_forecast=440.0), outcome_evidence=_MEETS_TARGET_EVIDENCE,
        store_context={"budget_constrained": True, "reallocation_candidate_share": 0.3},
    )
    assert rec["recommendation"] == "REALLOCATE_BUDGET"
    assert rec["requires_human_approval"] is True


def test_diversification_never_fires_without_proven_evidence():
    for evidence in (None, {"evidence_state": "INSUFFICIENT"},
                     {"evidence_state": "SUFFICIENT", "target_assessment": "NEGATIVE",
                      "actual_uplift_pct": -2.0}):
        rec = score_and_recommend(
            _signal(), outcome_evidence=evidence,
            store_context={"non_best_customer_household_share": 0.9},
        )
        assert "diversification" not in rec
        assert rec["recommendation"] != "RETARGET_SEGMENT"


def test_diversification_never_fires_without_context():
    rec = score_and_recommend(_signal(), outcome_evidence=_MEETS_TARGET_EVIDENCE)
    assert "diversification" not in rec


def test_below_threshold_context_keeps_base_decision():
    base = score_and_recommend(_signal(), outcome_evidence=_MEETS_TARGET_EVIDENCE)
    rec = score_and_recommend(
        _signal(), outcome_evidence=_MEETS_TARGET_EVIDENCE,
        store_context={"non_best_customer_household_share": 0.3},
    )
    assert rec["recommendation"] == base["recommendation"]
    assert rec["confidence"] == base["confidence"]


def test_all_new_action_types_are_approval_gated():
    for action in ("RETARGET_SEGMENT", "TIMING_SHIFT", "REALLOCATE_BUDGET"):
        assert requires_human_approval(action) is True

# --- Budget allocator --------------------------------------------------------

def _candidate(store_id, lift, confidence=0.8, eligible=True, state="SUFFICIENT",
               baseline_daily_sales=100.0, campaign_cost=0.0):
    return {
        "store_id": store_id, "expected_lift_pct": lift, "confidence": confidence,
        "is_eligible": eligible, "evidence_state": state,
        "baseline_daily_sales": baseline_daily_sales, "campaign_cost": campaign_cost,
    }


def test_allocator_scores_by_lift_times_confidence():
    # 5 stores, none individually above the 25% cap -> purely proportional.
    candidates = [_candidate(i, 2.0, 0.8) for i in (1, 2, 3, 4)] + [_candidate(5, 1.0, 1.0)]
    plan = allocate_budget(10000.0, candidates)
    allocs = {a.store_id: a.allocation for a in plan.allocations}
    # score 1.6 vs 1.0 -> 1.6/7.4 and 1.0/7.4 of the budget
    assert allocs[1] == pytest.approx(10000.0 * 1.6 / 7.4, abs=0.02)
    assert allocs[5] == pytest.approx(10000.0 * 1.0 / 7.4, abs=0.02)
    assert plan.allocated_budget == pytest.approx(10000.0, abs=0.05)
    assert plan.unallocated_budget == pytest.approx(0.0, abs=0.05)


def test_allocator_excludes_ineligible_and_unproven_stores():
    plan = allocate_budget(1000.0, [
        _candidate(1, 5.0, eligible=False),
        _candidate(2, 5.0, state="PARTIAL"),
        _candidate(3, -1.0),
        _candidate(4, 5.0),
    ])
    assert [a.store_id for a in plan.allocations] == [4]
    reasons = {e["store_id"]: e["reason"] for e in plan.excluded}
    assert reasons[1] == "not_eligible"
    assert reasons[2] == "evidence_state=PARTIAL"
    assert reasons[3] == "non_positive_expected_lift"


def test_allocator_ranks_by_margin_not_lift_percentage():
    # +1% lift on a high-volume store beats +5% on a low-volume store
    high_volume = _candidate(1, 1.0, 0.9, baseline_daily_sales=200.0)   # margin 30.00
    low_volume = _candidate(2, 5.0, 0.9, baseline_daily_sales=10.0)     # margin 7.50
    plan = allocate_budget(1000.0, [low_volume, high_volume])
    assert plan.allocations[0].store_id == 1
    assert plan.allocations[0].score == pytest.approx(30.0 * 0.9, abs=0.001)


def test_allocator_excludes_negative_expected_margin():
    # campaign cost exceeds the expected margin on a tiny-volume store
    plan = allocate_budget(1000.0, [
        _candidate(1, 1.0, 0.9, baseline_daily_sales=10.0, campaign_cost=50.0),
        _candidate(2, 2.0, 0.9),
    ])
    assert [a.store_id for a in plan.allocations] == [2]
    reasons = {e["store_id"]: e["reason"] for e in plan.excluded}
    assert reasons[1] == "non_positive_expected_margin"


def test_allocator_caps_single_store_share():
    plan = allocate_budget(1000.0, [
        _candidate(1, 100.0, 1.0),  # wildly dominant
        _candidate(2, 1.0, 0.5),
    ])
    by_store = {a.store_id: a for a in plan.allocations}
    assert by_store[1].capped is True
    assert by_store[1].allocation <= MAX_STORE_SHARE * 1000.0
    assert sum(a.allocation for a in plan.allocations) <= 1000.0
    assert by_store[2].allocation > 0


def test_allocator_is_deterministic_and_never_overspends():
    candidates = [_candidate(i, lift) for i, lift in enumerate([4.0, 8.0, 2.0, 6.0, 1.0], start=1)]
    p1 = allocate_budget(5000.0, candidates)
    p2 = allocate_budget(5000.0, list(reversed(candidates)))
    assert [(a.store_id, a.allocation) for a in p1.allocations] == \
           [(a.store_id, a.allocation) for a in p2.allocations]
    assert p1.allocated_budget <= 5000.0


def test_allocator_drops_dust_below_min_allocation():
    # tiny budget -> cap clamps the single store to 12.50 < MIN_ALLOCATION
    plan = allocate_budget(50.0, [_candidate(1, 0.001, 0.5)])
    assert plan.allocations == ()
    assert plan.allocated_budget == 0.0
    assert plan.excluded[0]["reason"] == "below_min_allocation"
    assert plan.unallocated_budget == pytest.approx(50.0)


def test_allocator_validates_inputs_fail_closed():
    with pytest.raises(BudgetAllocatorError):
        allocate_budget(0, [_candidate(1, 3.0)])
    with pytest.raises(BudgetAllocatorError):
        allocate_budget(-5, [_candidate(1, 3.0)])
    with pytest.raises(BudgetAllocatorError):
        allocate_budget(1000.0, [{"store_id": 1}])
    with pytest.raises(BudgetAllocatorError):
        allocate_budget(1000.0, [_candidate(1, 3.0, confidence=1.5)])
    with pytest.raises(BudgetAllocatorError):
        allocate_budget(1000.0, ["not-a-mapping"])
    with pytest.raises(BudgetAllocatorError):
        allocate_budget(True, [_candidate(1, 3.0)])


def test_allocator_empty_and_all_excluded_portfolio():
    plan = allocate_budget(1000.0, [])
    assert plan.allocations == () and plan.allocated_budget == 0.0
    plan = allocate_budget(1000.0, [_candidate(1, 3.0, eligible=False)])
    assert plan.allocations == () and plan.unallocated_budget == pytest.approx(1000.0)
    assert MIN_ALLOCATION > 0  # sanity: methodology constant present
