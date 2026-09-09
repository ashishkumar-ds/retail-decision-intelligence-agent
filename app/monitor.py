"""
Store Recovery Monitor — best-practice attention ranking for retail intelligence agent.

Purpose:
  - Answer: is target campaign working? (per-store health + measured lift + causal DiD)
  - Answer: should it get attention? (ranked attention queue, not just boolean flag)

Design (dunnhumby/84.51 grade):
  - Deterministic ranking, no LLM hallucination: priority is recomputable from
    recommendation fields + outcome evidence.
  - Fail-closed: missing fields never crash ranking; they deprioritize.
  - Human-gated: ranking never auto-approves; it only orders what humans must review.

Priority model (store recovery):
  tier 0 = must-act (ESCALATE, PAUSE_INTERVENTION) — campaign failing or stalled near deadline
  tier 1 = should-review (EXTEND_INTERVENTION, NEEDS_REVIEW, RETARGET/TIMING/REALLOCATE) — underperforming but window remains
  tier 2 = watch (MONITOR, CONTINUE) — on track

Within tier: urgency = (70-health)/70 + (60-days_remaining)/60 + (1-confidence)
  + NEGATIVE lift bonus (+2) + REFUTED causal bonus (+1)
  Lower health, fewer days, lower confidence, negative lift → higher urgency → rank 1.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

TIER = {
    "ESCALATE": 0,
    "PAUSE_INTERVENTION": 0,
    "EXTEND_INTERVENTION": 1,
    "NEEDS_REVIEW": 1,
    "RETARGET_SEGMENT": 1,
    "TIMING_SHIFT": 1,
    "REALLOCATE_BUDGET": 1,
    "MONITOR": 2,
    "CONTINUE": 2,
}

def _tier(rec: Mapping[str, Any]) -> int:
    return TIER.get(str(rec.get("recommendation", "")), 2)

def _urgency(rec: Mapping[str, Any]) -> float:
    try:
        health = float(rec.get("store_health_score", 50))
    except Exception:
        health = 50.0
    try:
        days = float(rec.get("days_remaining", 30))
    except Exception:
        days = 30.0
    try:
        conf = float(rec.get("confidence", 0.5))
    except Exception:
        conf = 0.5
    # clamp
    health = max(0.0, min(100.0, health))
    days = max(0.0, min(60.0, days))
    conf = max(0.0, min(1.0, conf))

    base = (70.0 - health) / 70.0 + (60.0 - days) / 60.0 + (1.0 - conf)

    # outcome bonus
    oe = rec.get("outcome_evidence") if isinstance(rec.get("outcome_evidence"), dict) else {}
    if oe.get("target_assessment") == "NEGATIVE":
        base += 2.0
    # causal refuted also urgent (controls outperformed)
    causal = oe.get("causal_evidence") if isinstance(oe.get("causal_evidence"), dict) else {}
    if causal.get("assessment_state") == "REFUTED":
        base += 1.0
    elif causal.get("assessment_state") == "REVIEW_ZONE":
        base += 0.3

    return round(float(base), 4)

def rank_attention(recommendations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """
    Return pending-approval recommendations sorted by attention priority.

    Only recommendations where requires_human_approval == True are ranked;
    others are excluded (they are MONITOR/CONTINUE — no attention).
    Pure function — deterministic sort: tier asc, urgency desc, store_id asc.
    """
    pending = [dict(r) for r in recommendations if r.get("requires_human_approval") is True]
    enriched: list[dict[str, Any]] = []
    for rec in pending:
        rec["attention_tier"] = _tier(rec)
        rec["attention_urgency"] = _urgency(rec)
        # human-readable reason for ranking
        if rec["attention_tier"] == 0:
            rec["attention_reason"] = "Must-act: failing or stalled near deadline"
        elif rec.get("outcome_evidence", {}).get("target_assessment") == "NEGATIVE":
            rec["attention_reason"] = "Prior intervention measured negative lift — pause/review"
        else:
            rec["attention_reason"] = "Should-review: underperforming with window remaining"
        enriched.append(rec)

    enriched.sort(key=lambda x: (x["attention_tier"], -x["attention_urgency"], int(x.get("store_id", 0))))
    for rank, rec in enumerate(enriched, start=1):
        rec["attention_rank"] = rank
    return enriched

def is_campaign_working(rec: Mapping[str, Any]) -> dict[str, Any]:
    """
    Answer: is target campaign working for this store?
    Returns {working: bool|None, evidence: str, uplift, causal}
    working=None means insufficient evidence (not yet due).
    """
    oe = rec.get("outcome_evidence") if isinstance(rec.get("outcome_evidence"), dict) else {}
    state = oe.get("evidence_state")
    if not state or state in ("NOT_DUE", "INSUFFICIENT", "PARTIAL", "INVALID", "CONTRADICTORY"):
        return {"working": None, "evidence": state or "no outcome yet", "detail": "await 56d baseline +14d recent; gaps are evidence"}
    uplift = oe.get("actual_uplift_pct")
    assess = oe.get("target_assessment")
    causal = oe.get("causal_evidence") if isinstance(oe.get("causal_evidence"), dict) else {}
    did = causal.get("did_uplift_pct")
    scale = causal.get("scale_up_eligible")
    if assess == "MEETS_TARGET" and causal.get("assessment_state") == "CONFIRMED":
        return {"working": True, "evidence": "MEETS_TARGET + CONFIRMED DiD", "uplift": uplift, "did": did, "scale_up_eligible": scale}
    if assess == "MEETS_TARGET":
        return {"working": True, "evidence": "MEETS_TARGET raw, causal not CONFIRMED — working but not causal", "uplift": uplift, "did": did, "scale_up_eligible": False}
    if assess == "NEGATIVE":
        return {"working": False, "evidence": "NEGATIVE lift vs own baseline", "uplift": uplift, "did": did}
    if assess == "REVIEW_ZONE":
        return {"working": None, "evidence": "REVIEW_ZONE 0-3% within noise", "uplift": uplift, "did": did}
    return {"working": None, "evidence": assess or "unknown", "uplift": uplift}
