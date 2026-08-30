"""Causal guardrail (Priority 3): treated-vs-control DiD assessment.

The feedback loop's `actual_uplift_pct` compares a store against its OWN
pre-intervention baseline. That is an operational metric, not a causal one:
the DiD notebook measured +9.7% market-wide drift during Campaign 18, which
own-baseline lift silently credits to the intervention. Scale-up decisions
(spending more, extending to more stores) therefore require the
treated-vs-matched-control difference-in-differences, served by Project 1's
`GET /controls/{store_id}`.

Policy (fail-closed):
- CONFIRMED   did_uplift_pct >= CAUSAL_TARGET_UPLIFT_PCT -> scale-up allowed
- REVIEW_ZONE 0 <= did_uplift_pct < target -> scale-up blocked, evidence mixed
- REFUTED     did_uplift_pct < 0 -> scale-up blocked, control group did better
- UNAVAILABLE no usable causal evidence -> scale-up blocked (never assumed)
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
    if did < 0:
        state = REFUTED
    elif did < target_pct:
        state = REVIEW_ZONE
    else:
        state = CONFIRMED
    return {"assessment_state": state, "scale_up_eligible": state == CONFIRMED,
            "did_uplift_pct": did, "target_pct": target_pct}
