# Decision-calibration constants - single source of truth for Part 1's
# causal evidence. The superseded LightGBM counterfactual (+30.1%) attributed
# market drift (+9.7%) to the campaign; the Part 1 Difference-in-Differences
# validation (household ITT, 981 clean controls, parallel-trends placebo
# p=0.897) puts true lift near +3% per 56-day cycle. Calibrating decisions to
# the causal estimate keeps EXTEND/ESCALATE rules and outcome evaluation from
# firing on healthy campaigns.

TARGET_UPLIFT_PCT = 3.0
REVIEW_ZONE_PCT = (0.0, 3.0)   # positive but within DiD noise - "promising, not proven"

CAUSAL_BASELINE = {
    "method": "Difference-in-Differences (household intent-to-treat)",
    "estimate_pct": 2.84,
    "ci95_pct": [-0.5, 6.2],
    "sensitivity_matched_pct": 7.03,
    "source": "Part 1 store performance notebook, DiD validation section",
}

# Health-score normalization: full recovery credit at the causal target,
# on-pace velocity = target spread over the 60-day recovery window.
HEALTH_RECOVERY_DIVISOR = TARGET_UPLIFT_PCT
HEALTH_VELOCITY_DIVISOR = TARGET_UPLIFT_PCT / 60.0
HEALTH_LOW, HEALTH_HIGH = 40.0, 70.0
