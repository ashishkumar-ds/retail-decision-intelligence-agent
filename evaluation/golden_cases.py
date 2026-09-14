"""Golden evaluation cases for the deterministic decision engine.

Each case pins the engine's CURRENT calibrated behavior for one business
scenario. This is a regression harness, not a spec generator: expected
values were derived from the shipped decision rules (scorer/calibration/
causality), so any code change that alters an outcome here must be a
deliberate, reviewed recalibration - update the case in the same commit
and say why.

Coverage: routing (no-data), health decision chain (CONTINUE / MONITOR /
EXTEND_INTERVENTION / ESCALATE), outcome feedback (NEGATIVE -> pause,
REVIEW_ZONE tempering, MEETS_TARGET causal guardrail: CONFIRMED / REVIEW
/ REFUTED / UNAVAILABLE), and diversified actions (RETARGET_SEGMENT /
TIMING_SHIFT / REALLOCATE_BUDGET, gated on context).

Run: ``python evaluation/run_evals.py`` (or via pytest:
``tests/test_golden_evals.py``).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GoldenCase:
    case_id: str
    description: str
    # StoreSignal fields
    baseline_forecast: float
    current_forecast: float
    days_elapsed: int
    days_remaining: int
    forecast_signal_available: bool = True
    # Optional scorer inputs
    outcome_evidence: dict | None = None
    causal_evidence: dict | None = None
    store_context: dict | None = None
    # Expectations (None = not asserted)
    expected_recommendation: str = ""
    expected_approval: bool | None = None
    expected_health: float | None = None
    expected_confidence: float | None = None
    expected_scale_up_eligible: bool | None = None
    expected_diversification: str | None = None
    tags: list[str] = field(default_factory=list)


# Healthy base signal used by several feedback cases: +4% at day 30 ->
# recovery component 50, velocity component 30, +20 signal credit = health 100.
_HEALTHY = dict(baseline_forecast=100.0, current_forecast=104.0, days_elapsed=30, days_remaining=30)
_SUFFICIENT = {"evidence_state": "SUFFICIENT", "intervention_id": "int-golden-1"}

@dataclass
class SimulationCase:
    """Pinned expectation for the pre-approval simulator (decision_engine.simulator)."""
    case_id: str
    description: str
    started_day: int
    observations: list  # [{"day": int, "sales_value": float}] (get_actuals shape)
    expected_evidence_state: str
    expected_guardrail_state: str | None = None
    expected_coverage_days: int | None = None
    expected_baseline_mean: float | None = None
    tags: list[str] = field(default_factory=list)


SIMULATION_CASES: list[SimulationCase] = [
    SimulationCase(
        case_id="sim_review_zone_full_coverage",
        description="Full 56d coverage, flat sales: prior point 2.84 lands REVIEW_ZONE - honest, not CONFIRMED",
        started_day=650,
        observations=[{"day": d, "sales_value": 100.0} for d in range(594, 650)],
        expected_evidence_state="SUFFICIENT",
        expected_guardrail_state="REVIEW_ZONE",
        expected_coverage_days=56,
        expected_baseline_mean=100.0,
        tags=["prior", "review-zone"],
    ),
    SimulationCase(
        case_id="sim_insufficient_coverage_fail_closed",
        description="Sparse baseline: INSUFFICIENT, guardrail UNAVAILABLE - never invents data",
        started_day=650,
        observations=[{"day": d, "sales_value": 100.0} for d in range(640, 650)],
        expected_evidence_state="INSUFFICIENT",
        expected_guardrail_state="UNAVAILABLE",
        tags=["fail-closed"],
    ),
    SimulationCase(
        case_id="sim_momentum_is_not_causal",
        description="Declining own sales (momentum -50%) must NOT change the causal projection",
        started_day=650,
        observations=[{"day": d, "sales_value": 200.0 if d < 622 else 100.0} for d in range(594, 650)],
        expected_evidence_state="SUFFICIENT",
        expected_guardrail_state="REVIEW_ZONE",
        expected_coverage_days=56,
        expected_baseline_mean=150.0,
        tags=["non-causal-separation"],
    ),
    SimulationCase(
        case_id="sim_window_filtering",
        description="Observations outside the pre-window are ignored (coverage stays 56)",
        started_day=650,
        observations=[{"day": d, "sales_value": 100.0} for d in range(590, 660)],
        expected_evidence_state="SUFFICIENT",
        expected_guardrail_state="REVIEW_ZONE",
        expected_coverage_days=56,
        tags=["window-filter"],
    ),
]


GOLDEN_CASES: list[GoldenCase] = [
    # --- Routing / no-data ----------------------------------------------------
    GoldenCase(
        case_id="no_data_flagged_for_review",
        description="Forecast signal unavailable - terminal NEEDS_REVIEW, never scored.",
        baseline_forecast=0, current_forecast=0, days_elapsed=30, days_remaining=30,
        forecast_signal_available=False,
        expected_recommendation="NEEDS_REVIEW", expected_approval=True,
        expected_health=0.0, expected_confidence=1.0,
        tags=["routing", "no_data"],
    ),
    # --- Health decision chain ------------------------------------------------
    GoldenCase(
        case_id="healthy_recovery_on_track",
        description="+4% recovery at day 30 - full marks, CONTINUE.",
        expected_recommendation="CONTINUE", expected_approval=False,
        expected_health=100.0, expected_confidence=1.0,
        tags=["health"],
        **_HEALTHY,
    ),
    GoldenCase(
        case_id="middling_signal_monitor",
        description="+1% at day 30 - health ~56.7, middling, MONITOR.",
        baseline_forecast=100.0, current_forecast=101.0, days_elapsed=30, days_remaining=30,
        expected_recommendation="MONITOR", expected_approval=False,
        expected_health=56.7,
        tags=["health"],
    ),
    GoldenCase(
        case_id="underperforming_with_time_left_extend",
        description="+0.5% at day 45 - health 35 (<40) with 15 days left, EXTEND_INTERVENTION.",
        baseline_forecast=100.0, current_forecast=100.5, days_elapsed=45, days_remaining=15,
        expected_recommendation="EXTEND_INTERVENTION", expected_approval=True,
        expected_health=35.0,
        tags=["health", "approval"],
    ),
    GoldenCase(
        case_id="underperforming_near_deadline_escalate",
        description="+0.5% at day 47 - health <40 with 13 days left, ESCALATE.",
        baseline_forecast=100.0, current_forecast=100.5, days_elapsed=47, days_remaining=13,
        expected_recommendation="ESCALATE", expected_approval=True,
        tags=["health", "approval"],
    ),
    GoldenCase(
        case_id="zero_recovery_long_window_extend",
        description="Flat at day 30 - health 20, 30 days left, EXTEND_INTERVENTION.",
        baseline_forecast=100.0, current_forecast=100.0, days_elapsed=30, days_remaining=30,
        expected_recommendation="EXTEND_INTERVENTION", expected_approval=True,
        expected_health=20.0,
        tags=["health", "approval"],
    ),
    GoldenCase(
        case_id="zero_elapsed_monitor",
        description="+2% but zero days elapsed - velocity undefined, health ~53.3, MONITOR.",
        baseline_forecast=100.0, current_forecast=102.0, days_elapsed=0, days_remaining=60,
        expected_recommendation="MONITOR", expected_approval=False,
        expected_health=53.3,
        tags=["health"],
    ),
    GoldenCase(
        case_id="above_target_continue",
        description="+3.5% across the full window - target met with room, CONTINUE.",
        baseline_forecast=100.0, current_forecast=103.5, days_elapsed=60, days_remaining=0,
        expected_recommendation="CONTINUE", expected_approval=False,
        tags=["health"],
    ),
    # --- Outcome feedback loop -------------------------------------------------
    GoldenCase(
        case_id="negative_lift_pauses_intervention",
        description="Prior intervention measured -2% lift - pause it (approval-gated).",
        expected_recommendation="PAUSE_INTERVENTION", expected_approval=True,
        expected_confidence=1.0,
        outcome_evidence={**_SUFFICIENT, "actual_uplift_pct": -2.0, "target_assessment": "NEGATIVE"},
        tags=["feedback", "approval"],
        **_HEALTHY,
    ),
    GoldenCase(
        case_id="negative_lift_on_middling_store_pauses",
        description="NEGATIVE lift overrides a middling health signal - pause.",
        baseline_forecast=100.0, current_forecast=101.0, days_elapsed=30, days_remaining=30,
        expected_recommendation="PAUSE_INTERVENTION", expected_approval=True,
        expected_confidence=0.83,
        outcome_evidence={**_SUFFICIENT, "actual_uplift_pct": -1.0, "target_assessment": "NEGATIVE"},
        tags=["feedback", "approval"],
    ),
    GoldenCase(
        case_id="review_zone_lift_tempers_confidence",
        description="+1.5% raw lift is within DiD noise - recommendation unchanged, confidence x0.8.",
        expected_recommendation="CONTINUE", expected_approval=False,
        expected_confidence=0.8,
        outcome_evidence={**_SUFFICIENT, "actual_uplift_pct": 1.5, "target_assessment": "REVIEW_ZONE"},
        tags=["feedback"],
        **_HEALTHY,
    ),
    GoldenCase(
        case_id="meets_target_without_causal_blocks_scaleup",
        description="Own-baseline lift meets target but no DiD - scale-up held (fail-closed).",
        expected_recommendation="CONTINUE", expected_approval=False,
        expected_scale_up_eligible=False,
        outcome_evidence={**_SUFFICIENT, "actual_uplift_pct": 3.5, "target_assessment": "MEETS_TARGET"},
        tags=["feedback", "causal_guardrail"],
        **_HEALTHY,
    ),
    GoldenCase(
        case_id="meets_target_did_confirmed_scaleup_eligible",
        description="DiD +4% confirms the lift - confidence boosted, scale-up eligible.",
        expected_recommendation="CONTINUE", expected_approval=False,
        expected_confidence=1.0, expected_scale_up_eligible=True,
        outcome_evidence={**_SUFFICIENT, "actual_uplift_pct": 3.5, "target_assessment": "MEETS_TARGET"},
        causal_evidence={"evidence_state": "SUFFICIENT", "did_uplift_pct": 4.0},
        tags=["feedback", "causal_guardrail"],
        **_HEALTHY,
    ),
    GoldenCase(
        case_id="meets_target_did_review_zone_scaleup_blocked",
        description="Raw lift meets target but DiD +1% is review zone - scale-up blocked, no boost.",
        expected_recommendation="CONTINUE", expected_approval=False,
        expected_scale_up_eligible=False,
        outcome_evidence={**_SUFFICIENT, "actual_uplift_pct": 3.5, "target_assessment": "MEETS_TARGET"},
        causal_evidence={"evidence_state": "SUFFICIENT", "did_uplift_pct": 1.0},
        tags=["feedback", "causal_guardrail"],
        **_HEALTHY,
    ),
    GoldenCase(
        case_id="meets_target_did_refuted_tempers_confidence",
        description="Controls outperformed (DiD -1%) - scale-up blocked, confidence x0.8.",
        expected_recommendation="CONTINUE", expected_approval=False,
        expected_confidence=0.8, expected_scale_up_eligible=False,
        outcome_evidence={**_SUFFICIENT, "actual_uplift_pct": 3.5, "target_assessment": "MEETS_TARGET"},
        causal_evidence={"evidence_state": "SUFFICIENT", "did_uplift_pct": -1.0},
        tags=["feedback", "causal_guardrail"],
        **_HEALTHY,
    ),
    GoldenCase(
        case_id="insufficient_outcome_changes_nothing",
        description="INSUFFICIENT outcome evidence - recommendation and confidence unchanged.",
        expected_recommendation="CONTINUE", expected_approval=False,
        expected_confidence=1.0, expected_scale_up_eligible=None,
        outcome_evidence={"evidence_state": "INSUFFICIENT", "intervention_id": "int-golden-1"},
        tags=["feedback"],
        **_HEALTHY,
    ),
    GoldenCase(
        case_id="not_due_outcome_changes_nothing",
        description="Outcome not yet due - no modulation at all.",
        expected_recommendation="CONTINUE", expected_approval=False,
        outcome_evidence={"evidence_state": "NOT_DUE", "intervention_id": "int-golden-1"},
        tags=["feedback"],
        **_HEALTHY,
    ),
    # --- Diversified actions (proven play + explicit context) -------------------
    GoldenCase(
        case_id="diversify_retarget_large_uncovered_segment",
        description="Proven play + 60% of households outside Best Customer - RETARGET_SEGMENT.",
        expected_recommendation="RETARGET_SEGMENT", expected_approval=True,
        expected_diversification="RETARGET_SEGMENT",
        outcome_evidence={**_SUFFICIENT, "actual_uplift_pct": 3.5, "target_assessment": "MEETS_TARGET"},
        causal_evidence={"evidence_state": "SUFFICIENT", "did_uplift_pct": 4.0},
        store_context={"non_best_customer_household_share": 0.6},
        tags=["diversification", "approval"],
        **_HEALTHY,
    ),
    GoldenCase(
        case_id="diversify_timing_shift_concentrated_sales",
        description="Proven play + 45% peak-hour concentration - TIMING_SHIFT.",
        expected_recommendation="TIMING_SHIFT", expected_approval=True,
        expected_diversification="TIMING_SHIFT",
        outcome_evidence={**_SUFFICIENT, "actual_uplift_pct": 3.5, "target_assessment": "MEETS_TARGET"},
        causal_evidence={"evidence_state": "SUFFICIENT", "did_uplift_pct": 4.0},
        store_context={"peak_hour_sales_concentration": 0.45, "peak_hour_window": "12 PM - 6 PM"},
        tags=["diversification", "approval"],
        **_HEALTHY,
    ),
    GoldenCase(
        case_id="diversify_reallocate_budget_when_confident",
        description="Proven play, confident store, constrained budget - REALLOCATE_BUDGET.",
        expected_recommendation="REALLOCATE_BUDGET", expected_approval=True,
        expected_diversification="REALLOCATE_BUDGET",
        outcome_evidence={**_SUFFICIENT, "actual_uplift_pct": 3.5, "target_assessment": "MEETS_TARGET"},
        causal_evidence={"evidence_state": "SUFFICIENT", "did_uplift_pct": 4.0},
        store_context={"budget_constrained": True, "reallocation_candidate_share": 0.2},
        tags=["diversification", "approval"],
        **_HEALTHY,
    ),
    GoldenCase(
        case_id="no_diversify_small_uncovered_segment",
        description="Only 30% uncovered share - below the 50% retarget bar, decision unchanged.",
        expected_recommendation="CONTINUE", expected_approval=False,
        expected_diversification=None,
        outcome_evidence={**_SUFFICIENT, "actual_uplift_pct": 3.5, "target_assessment": "MEETS_TARGET"},
        causal_evidence={"evidence_state": "SUFFICIENT", "did_uplift_pct": 4.0},
        store_context={"non_best_customer_household_share": 0.3},
        tags=["diversification"],
        **_HEALTHY,
    ),
    GoldenCase(
        case_id="no_diversify_without_store_context",
        description="Proven play but no store context - decision unchanged (backward compatible).",
        expected_recommendation="CONTINUE", expected_approval=False,
        expected_diversification=None,
        outcome_evidence={**_SUFFICIENT, "actual_uplift_pct": 3.5, "target_assessment": "MEETS_TARGET"},
        causal_evidence={"evidence_state": "SUFFICIENT", "did_uplift_pct": 4.0},
        store_context=None,
        tags=["diversification"],
        **_HEALTHY,
    ),
]
