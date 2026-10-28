"""Causal guardrail (Priority 3): treated-vs-control DiD assessment.

The feedback loop's `actual_uplift_pct` compares a store against its OWN
pre-intervention baseline. That is an operational metric, not a causal one:
the DiD notebook measured +9.7% market-wide drift during Campaign 18, which
own-baseline lift silently credits to the intervention. Scale-up decisions
(spending more, extending to more stores) therefore require the
treated-vs-matched-control difference-in-differences, served by Project 1's
`GET /controls/{store_id}`.

Policy (fail-closed):

- CONFIRMED   did point >= target AND (no usable CI, or CI lower bound >= 0
              AND point >= MDE when both are supplied) -> scale-up allowed.
              A usable CI whose lower bound dips below zero, or a point below
              the minimum detectable effect, downgrades to REVIEW_ZONE:
              directionally positive but not proven.
- REVIEW_ZONE 0 <= did_uplift_pct < target -> scale-up blocked, evidence mixed
- REFUTED     did_uplift_pct < 0 -> scale-up blocked, control group did better
- UNAVAILABLE no usable causal evidence -> scale-up blocked (never assumed)

The controls response also carries report-only diagnostics (did_ci95,
mde_pct, placebo_did_pct, pre_trend_divergence, control pool). The CI lower
bound and MDE move the assessment above (CONFIRMED -> REVIEW_ZONE only,
never upward); placebo / pre-trend stay report-only until calibrated.
"""

from __future__ import annotations

from typing import Any, Mapping

from decision_engine.calibration import TARGET_UPLIFT_PCT

CONFIRMED = "CONFIRMED"
REVIEW_ZONE = "REVIEW_ZONE"
REFUTED = "REFUTED"
UNAVAILABLE = "UNAVAILABLE"


def assess_causal_evidence(causal_evidence: Mapping[str, Any] | None,
                           target_pct: float = TARGET_UPLIFT_PCT) -> dict:
    """Assess matched-control DiD evidence; returns state + scale-up eligibility.

    Never raises for business-level absence of evidence - missing, malformed,
    or non-sufficient evidence is UNAVAILABLE (fail-closed), because scale-up
    must not proceed on an assumption.
    """
    if not isinstance(causal_evidence, Mapping):
        return {"assessment_state": UNAVAILABLE, "scale_up_eligible": False,
                "did_uplift_pct": None, "target_pct": target_pct}
    if causal_evidence.get("evidence_state") != "SUFFICIENT":
        return {"assessment_state": UNAVAILABLE, "scale_up_eligible": False,
                "did_uplift_pct": None, "target_pct": target_pct,
                "reason": f"evidence_state={causal_evidence.get('evidence_state')}"}
    did = causal_evidence.get("did_uplift_pct")
    if isinstance(did, bool) or not isinstance(did, (int, float)):
        return {"assessment_state": UNAVAILABLE, "scale_up_eligible": False,
                "did_uplift_pct": None, "target_pct": target_pct,
                "reason": "did_uplift_pct missing or non-numeric"}
    did = float(did)
    downgrade_reason: str | None = None
    if did < 0:
        state = REFUTED
    elif did < target_pct:
        state = REVIEW_ZONE
    else:
        state = CONFIRMED
        # Uncertainty downgrade (P2): the point clears the bar, but the band
        # does not rule out zero (or the effect is below what the design can
        # detect). Downgrade only — diagnostics never promote.
        downgrade_reason = _uncertainty_downgrade_reason(causal_evidence, did)
        if downgrade_reason is not None:
            state = REVIEW_ZONE
    result = {"assessment_state": state, "scale_up_eligible": state == CONFIRMED,
              "did_uplift_pct": did, "target_pct": target_pct}
    if downgrade_reason is not None:
        result["downgrade_reason"] = downgrade_reason
    # Pass through supplied diagnostics so callers can cite them.
    for key in ("did_ci95", "mde_pct", "placebo_did_pct", "pre_trend_divergence"):
        value = causal_evidence.get(key)
        if value is not None:
            result[key] = value
    return result


def _uncertainty_downgrade_reason(causal_evidence: Mapping[str, Any], did: float) -> str | None:
    """Why a target-clearing point is still only REVIEW_ZONE, else None.

    - Usable CI (two finite numbers) with lower bound < 0: the band admits
      no effect — the direction is not established.
    - Numeric MDE with point < MDE: below the design's detection floor.
    Missing/malformed diagnostics -> no downgrade (point rule stands).
    """
    ci = causal_evidence.get("did_ci95")
    if isinstance(ci, (list, tuple)) and len(ci) == 2:
        try:
            low = float(ci[0])
        except (TypeError, ValueError):
            low = None
        if low is not None and low == low and low not in (float("inf"), float("-inf")) and low < 0:
            return f"CI-95 lower bound {low}% admits no effect"
    mde = causal_evidence.get("mde_pct")
    if isinstance(mde, bool):
        return None
    if isinstance(mde, (int, float)) and mde == mde and did < float(mde):
        return f"point {did}% below MDE {float(mde)}%"
    return None
