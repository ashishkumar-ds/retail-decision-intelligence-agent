"""Tests for the store-level retail context layer (tools/retail_context.py).

Covers: margin proxy from P2 store totals, availability proxy from actuals
coverage, scorer annotation (never flips the label), and the per-candidate
margin_rate override in the budget allocator.
"""
import pytest

from decision_engine.scorer import StoreSignal, score_and_recommend
from phase2.budget_allocator import BudgetAllocatorError, allocate_budget
from tools.retail_context import (
    annotate_retail_context,
    assess_availability,
    derive_store_margin,
)


def _signal(store_id=317):
    return StoreSignal(
        store_id=store_id, baseline_forecast=100.0, current_forecast=100.0,
        days_elapsed=30, days_remaining=30,
        forecast_signal_available=True, forecast_status="AVAILABLE",
    )


# --- derive_store_margin ---

def test_margin_proxy_from_p2_row():
    margin = derive_store_margin(
        {"SALES_VALUE": 52117.34, "QUANTITY": 24934, "RETAIL_DISC": -13748.73}
    )
    assert margin["discount_rate"] == pytest.approx(13748.73 / 52117.34, abs=1e-4)
    assert margin["margin_rate"] == pytest.approx(0.35 - 13748.73 / 52117.34, abs=1e-4)
    assert margin["avg_unit_value"] == pytest.approx(52117.34 / 24934, abs=1e-4)


def test_margin_clamps_and_rejects_garbage():
    assert derive_store_margin({"SALES_VALUE": 0, "QUANTITY": 10}) is None
    assert derive_store_margin({"SALES_VALUE": 100}) is None
    assert derive_store_margin("not-a-mapping") is None
    rich = derive_store_margin({"SALES_VALUE": 100.0, "QUANTITY": 10, "RETAIL_DISC": 0.0})
    assert rich["margin_rate"] == pytest.approx(0.35)


# --- assess_availability ---

def test_availability_ok_and_supply_gap():
    ok = assess_availability(50, 56, 12, 14)
    assert ok["availability_state"] == "OK"
    gap = assess_availability(50, 56, 5, 14)
    assert gap["availability_state"] == "POSSIBLE_SUPPLY_GAP"
    unknown = assess_availability(0, 0, 0, 0)
    assert unknown["availability_state"] == "UNKNOWN"


def test_availability_rejects_bad_types():
    with pytest.raises(TypeError):
        assess_availability(True, 56, 5, 14)


# --- annotate_retail_context ---

def test_annotate_combines_margin_and_gap():
    block = annotate_retail_context(
        317,
        {"SALES_VALUE": 52117.34, "QUANTITY": 24934, "RETAIL_DISC": -13748.73},
        {"availability_state": "POSSIBLE_SUPPLY_GAP",
         "baseline_coverage_ratio": 0.9, "recent_coverage_ratio": 0.3},
    )
    assert block["demand_vs_supply"] == "POSSIBLE_SUPPLY_GAP"
    assert block["margin_rate"] is not None
    assert "verify stock" in block["ops_flag"]


def test_annotate_none_when_no_data():
    assert annotate_retail_context(317, None, None) is None


# --- scorer: annotation never flips the label ---

def test_scorer_supply_gap_tempers_confidence_without_flipping():
    base = score_and_recommend(_signal())
    flagged = score_and_recommend(
        _signal(),
        retail_context={"demand_vs_supply": "POSSIBLE_SUPPLY_GAP",
                        "margin_rate": 0.09, "discount_rate": 0.26,
                        "avg_unit_value": 2.09, "ops_flag": "check ops"},
    )
    assert flagged["recommendation"] == base["recommendation"]
    assert flagged["confidence"] == pytest.approx(round(base["confidence"] * 0.9, 2))
    assert "supply/coverage gap" in flagged["reason"]
    assert flagged["retail_context"]["demand_vs_supply"] == "POSSIBLE_SUPPLY_GAP"


def test_scorer_without_context_is_byte_identical():
    a = score_and_recommend(_signal())
    b = score_and_recommend(_signal(), retail_context=None)
    assert a["recommendation"] == b["recommendation"]
    assert a["confidence"] == b["confidence"]
    assert "retail_context" not in b


# --- allocator: per-store margin_rate override ---

def _candidate(store_id, lift, confidence=0.9, margin_rate=None):
    cand = {
        "store_id": store_id, "expected_lift_pct": lift, "confidence": confidence,
        "is_eligible": True, "evidence_state": "SUFFICIENT",
        "baseline_daily_sales": 100.0, "campaign_cost": 0.0,
    }
    if margin_rate is not None:
        cand["margin_rate"] = margin_rate
    return cand


def test_allocator_uses_store_margin_override():
    plan = allocate_budget(1000.0, [_candidate(1, 2.0, margin_rate=0.10)])
    # margin = 100*60*2/100*0.10 = 12.0; score = 12*0.9 = 10.8
    assert plan.allocations[0].score == pytest.approx(10.8, abs=1e-3)


def test_allocator_default_margin_unchanged():
    plan = allocate_budget(1000.0, [_candidate(1, 2.0)])
    # default 0.25: margin = 100*60*2/100*0.25 = 30.0; score = 27.0
    assert plan.allocations[0].score == pytest.approx(27.0, abs=1e-3)


def test_allocator_rejects_bad_margin_rate():
    with pytest.raises(BudgetAllocatorError):
        allocate_budget(1000.0, [_candidate(1, 2.0, margin_rate=1.5)])
    with pytest.raises(BudgetAllocatorError):
        allocate_budget(1000.0, [_candidate(1, 2.0, margin_rate=0)])
