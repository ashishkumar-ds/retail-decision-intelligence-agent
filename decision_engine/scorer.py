# Scorer - computes recovery_pct, recovery_velocity, health_score, and
# applies the decision rule chain that produces a recommendation.
from dataclasses import dataclass
from datetime import datetime, timezone

from decision_engine.calibration import (
    CAUSAL_BASELINE,
    HEALTH_HIGH,
    HEALTH_LOW,
    HEALTH_RECOVERY_DIVISOR,
    HEALTH_VELOCITY_DIVISOR,
    TARGET_UPLIFT_PCT,
)
from decision_engine.causality import (
    CONFIRMED,
    REFUTED,
    REVIEW_ZONE,
    assess_causal_evidence,
)
from guardrails import requires_human_approval


@dataclass
class StoreSignal:
    store_id: int
    baseline_forecast: float
    current_forecast: float
    days_elapsed: int
    days_remaining: int
    forecast_signal_available: bool
    forecast_status: str = "AVAILABLE"


def compute_recovery_pct(signal: StoreSignal) -> float:
    if signal.baseline_forecast <= 0:
        return 0.0
    return (signal.current_forecast - signal.baseline_forecast) / signal.baseline_forecast * 100


def compute_recovery_velocity(recovery_pct: float, days_elapsed: int) -> float:
    if days_elapsed <= 0:
        return 0.0
    return recovery_pct / days_elapsed


def compute_health_score(recovery_pct: float, recovery_velocity: float, signal_available: bool) -> float:
    # Deterministic, interpretable heuristic (a reviewer can recompute by
    # hand) - not a calibrated probability of recovery. Normalized against
    # the DiD causal target (~3%/cycle), not the superseded +30.1% forecast.
    recovery_component = min(max(recovery_pct / HEALTH_RECOVERY_DIVISOR, 0.0), 1.0) * 50
    velocity_component = min(max(recovery_velocity / HEALTH_VELOCITY_DIVISOR, 0.0), 1.0) * 30
    completeness_component = 20.0 if signal_available else 0.0
    return round(recovery_component + velocity_component + completeness_component, 1)


def _boundary_confidence(health_score: float) -> float:
    """Lower confidence the closer the score sits to a decision boundary (40 or 70)."""
    distances = [abs(health_score - b) for b in (HEALTH_LOW, HEALTH_HIGH)]
    nearest = min(distances)
    return round(min(0.5 + nearest / 40, 1.0), 2)


def _recovery_direction(recovery_pct: float) -> str:
    """Signed-momentum label from the (unclamped) recovery %.

    The health *score* intentionally clamps negative recovery at zero, so a
    declining store and a merely flat store both contribute 0 to the score's
    recovery component. That keeps the bounded score a reviewer can recompute
    by hand, but it hides the sign of the move. This field surfaces the signed
    signal explicitly so a flat store and a declining store are distinguishable
    in the record, not just in downstream velocity rules.
    """
    if recovery_pct < 0:
        return "declining"
    if recovery_pct > 0:
        return "recovering"
    return "flat"


def no_data_recommendation(signal: StoreSignal) -> dict:
    # Terminal case for the "no_data" route - skips recovery/velocity/
    # health computation entirely since there's no data to compute it from.
    return _build_output(
        signal, health_score=0.0, recovery_pct=0.0,
        recommendation="NEEDS_REVIEW", confidence=1.0,
        reason=f"No forecast signal available for store {signal.store_id} - cannot evaluate without data.",
        requires_approval=requires_human_approval("NEEDS_REVIEW"),
    )


def _health_recommendation(
    signal: StoreSignal, health: float, recovery_pct: float, velocity: float
) -> tuple[str, str]:
    """Deterministic health-based decision rules; first match wins."""
    if health >= 70:
        return "CONTINUE", (
            f"Store {signal.store_id} health score {health} "
            f"(recovery {recovery_pct:.1f}%) - on track, no action needed."
        )
    if health < 40 and signal.days_remaining < 14:
        return "ESCALATE", (
            f"Store {signal.store_id} health score {health} with only {signal.days_remaining} days "
            f"remaining - unlikely to hit the 2-month target without intervention."
        )
    if health < 40:
        return "EXTEND_INTERVENTION", (
            f"Store {signal.store_id} health score {health} - underperforming, "
            f"but {signal.days_remaining} days remain to recover."
        )
    if velocity <= 0 and signal.days_remaining < 30:
        return "ESCALATE", (
            f"Store {signal.store_id} recovery has stalled (velocity {velocity:.2f}%/day) "
            f"with {signal.days_remaining} days left."
        )
    return "MONITOR", (
        f"Store {signal.store_id} health score {health} - middling signal, continue watching."
    )


def score_and_recommend(signal: StoreSignal, outcome_evidence: dict | None = None,
                        store_context: dict | None = None,
                        causal_evidence: dict | None = None) -> dict:
    # Decision rules applied in order; first match wins.
    if not signal.forecast_signal_available:
        return no_data_recommendation(signal)

    recovery_pct = compute_recovery_pct(signal)
    velocity = compute_recovery_velocity(recovery_pct, signal.days_elapsed)
    health = compute_health_score(recovery_pct, velocity, signal.forecast_signal_available)
    rec, reason = _health_recommendation(signal, health, recovery_pct, velocity)

    confidence = _boundary_confidence(health)

    # Outcome feedback loop: a previously evaluated intervention for this store
    # is causal evidence about whether the strategy is working. Measured lift
    # (actual observed sales vs the 56-day pre-intervention baseline) adjusts
    # the recommendation - closing the plan -> execute -> measure -> re-decide
    # cycle.
    outcome_context = None
    if isinstance(outcome_evidence, dict) and outcome_evidence.get("evidence_state") == "SUFFICIENT":
        rec, reason, confidence, outcome_context = _apply_outcome_feedback(
            signal, health, rec, reason, confidence, outcome_evidence, causal_evidence,
        )
    elif isinstance(outcome_evidence, dict) and outcome_evidence.get("evidence_state"):
        outcome_context = {
            "intervention_id": outcome_evidence.get("intervention_id"),
            "evidence_state": outcome_evidence.get("evidence_state"),
        }

    # Diversified policy (Priority 2): when the prior intervention MEASURED a
    # lift that meets the causal target, the current play is proven - so it is
    # the right moment to diversify into a complementary action rather than
    # only extending more of the same. Requires explicit store context; with
    # no context the decision is unchanged (backward compatible).
    diversification = None
    if _diversification_due(outcome_evidence, store_context):
        diversified = _diversified_action(signal, health, recovery_pct, velocity, store_context)
        if diversified is not None:
            rec, reason, confidence, diversification = diversified

    return _build_output(
        signal, health_score=health, recovery_pct=recovery_pct,
        recommendation=rec, confidence=confidence, reason=reason,
        requires_approval=requires_human_approval(rec),
        outcome_context=outcome_context,
        diversification=diversification,
        recovery_velocity=velocity,
        scale_up_eligible=(outcome_context.get("causal_evidence", {}).get("scale_up_eligible")
                           if isinstance(outcome_context, dict) and "causal_evidence" in outcome_context
                           else None),
    )


def _apply_outcome_feedback(
    signal: StoreSignal,
    health: float,
    rec: str,
    reason: str,
    confidence: float,
    outcome_evidence: dict,
    causal_evidence: dict | None,
) -> tuple[str, str, float, dict]:
    """Modulate the health decision with measured outcome evidence.

    Evidence only ever modulates; it never fabricates a decision the health
    rules wouldn't make, and inconclusive evidence changes nothing.
    """
    lift = outcome_evidence.get("actual_uplift_pct")
    assessment = outcome_evidence.get("target_assessment")
    outcome_context = {
        "intervention_id": outcome_evidence.get("intervention_id"),
        "actual_uplift_pct": lift,
        "target_assessment": assessment,
    }
    if assessment == "NEGATIVE" and (isinstance(lift, (int, float)) and lift < 0):
        # The evaluated intervention made things worse - recommend pausing
        # it rather than extending more of the same (human approval still
        # required: pausing a live campaign is a commercial action).
        return "PAUSE_INTERVENTION", (
            f"Store {signal.store_id} health score {health}, but the prior intervention "
            f"measured {lift:+.1f}% observed sales lift vs its 56-day baseline "
            f"(NEGATIVE) - pause the campaign rather than extend it."
        ), max(confidence, 0.7), outcome_context
    if assessment == "REVIEW_ZONE" and isinstance(lift, (int, float)):
        return rec, (
            reason + (
                f" Prior intervention measured {lift:+.1f}% lift - positive but within "
                f"DiD noise (review zone), so confidence is tempered."
            )
        ), round(confidence * 0.8, 2), outcome_context
    if assessment == "MEETS_TARGET" and isinstance(lift, (int, float)):
        return _apply_causal_guardrail(
            rec, reason, confidence, lift, outcome_context, causal_evidence,
        )
    return rec, reason, confidence, outcome_context


def _apply_causal_guardrail(
    rec: str,
    reason: str,
    confidence: float,
    lift: float,
    outcome_context: dict,
    causal_evidence: dict | None,
) -> tuple[str, str, float, dict]:
    """Causal guardrail (Priority 3): raw own-baseline lift meeting the
    target is NOT sufficient for scale-up. Only confirmed
    treated-vs-matched-control (DiD) lift may drive scale-up; the confidence
    boost is withheld otherwise (fail-closed)."""
    causal = assess_causal_evidence(causal_evidence)
    causal_state = causal["assessment_state"]
    causal_context = {
        "assessment_state": causal_state,
        "did_uplift_pct": causal["did_uplift_pct"],
        "scale_up_eligible": causal["scale_up_eligible"],
    }
    outcome_context["causal_evidence"] = causal_context
    if causal_state == CONFIRMED:
        confidence = min(round(confidence + 0.2, 2), 1.0)
        reason += (f" Prior intervention measured {lift:+.1f}% observed lift, meeting the "
                   f"{TARGET_UPLIFT_PCT}% causal target, and matched-control DiD confirms "
                   f"{causal['did_uplift_pct']:+.1f}% treated-vs-control lift - evidence-backed; "
                   f"scale-up eligible.")
    elif causal_state == REVIEW_ZONE:
        reason += (f" Prior intervention measured {lift:+.1f}% raw lift, but matched-control "
                   f"DiD shows only {causal['did_uplift_pct']:+.1f}% - below the "
                   f"{TARGET_UPLIFT_PCT}% target, so scale-up is blocked (review zone).")
    elif causal_state == REFUTED:
        confidence = round(confidence * 0.8, 2)
        reason += (f" Prior intervention measured {lift:+.1f}% raw lift, but matched-control "
                   f"DiD is {causal['did_uplift_pct']:+.1f}% (controls outperformed) - causal "
                   f"guardrail blocks scale-up and tempers confidence.")
    else:
        reason += (f" Prior intervention measured {lift:+.1f}% raw lift vs its own baseline, "
                   f"but no matched-control DiD confirmation is available - scale-up held "
                   f"pending causal evidence (own-baseline lift is not causal).")
    return rec, reason, confidence, outcome_context


def _diversification_due(outcome_evidence: dict | None, store_context: dict | None) -> bool:
    """Diversify only when the proven (MEETS_TARGET) play is confirmed and
    explicit store context exists; with no context the decision is unchanged
    (backward compatible)."""
    return (
        isinstance(outcome_evidence, dict)
        and outcome_evidence.get("evidence_state") == "SUFFICIENT"
        and outcome_evidence.get("target_assessment") == "MEETS_TARGET"
        and isinstance(store_context, dict)
    )


# --- Diversified action rules (Priority 2) ---------------------------------
# Each rule fires ONLY on top of proven (MEETS_TARGET) evidence and explicit
# store context. First match wins, mirroring the main decision chain.
RETARGET_SEGMENT_MIN_SHARE = 0.50   # >=50% of a store's households are not yet
                                    # in the "Best Customer" RFM segment
TIMING_SHIFT_MIN_CONCENTRATION = 0.40  # >=40% of daily sales inside the peak
                                       # hour window -> shifting send time has
                                       # measurable headroom
REALLOCATE_BUDGET_MIN_CONFIDENCE = 0.7  # only move budget toward stores we
                                        # are confident about


def _diversified_action(signal: StoreSignal, health: float, recovery_pct: float,
                        velocity: float, ctx: dict) -> tuple | None:
    share = ctx.get("non_best_customer_household_share")
    if isinstance(share, (int, float)) and share >= RETARGET_SEGMENT_MIN_SHARE:
        return (
            "RETARGET_SEGMENT",
            (f"Store {signal.store_id} health score {health}, but {share:.0%} of its households "
             f"are outside the Best Customer segment while the current play measured a lift "
             f"meeting the {TARGET_UPLIFT_PCT}% target - retarget the remaining segment "
             f"(P1: {ctx.get('candidate_segment', 'non-Best-Customer households')}) rather than extend again."),
            max(_boundary_confidence(health), 0.7),
            {"action": "RETARGET_SEGMENT", "non_best_customer_household_share": round(float(share), 2)},
        )
    concentration = ctx.get("peak_hour_sales_concentration")
    peak_window = ctx.get("peak_hour_window")
    if (isinstance(concentration, (int, float)) and concentration >= TIMING_SHIFT_MIN_CONCENTRATION
            and isinstance(peak_window, str) and peak_window):
        return (
            "TIMING_SHIFT",
            (f"Store {signal.store_id} health score {health}, but {concentration:.0%} of its daily sales "
             f"concentrate in the {peak_window} window while the current play already meets the "
             f"{TARGET_UPLIFT_PCT}% target - shift the campaign send time into the measured peak "
             f"instead of spending more."),
            max(_boundary_confidence(health), 0.7),
            {"action": "TIMING_SHIFT", "peak_hour_window": peak_window,
             "peak_hour_sales_concentration": round(float(concentration), 2)},
        )
    constrained = ctx.get("budget_constrained") is True
    candidate_share = ctx.get("reallocation_candidate_share")
    if constrained and isinstance(candidate_share, (int, float)) and candidate_share > 0:
        # Reallocation is only sensible when we are confident in the source store
        if _boundary_confidence(health) >= REALLOCATE_BUDGET_MIN_CONFIDENCE:
            return (
                "REALLOCATE_BUDGET",
                (f"Store {signal.store_id} health score {health} and its play already meets the "
                 f"{TARGET_UPLIFT_PCT}% target with confidence >= {REALLOCATE_BUDGET_MIN_CONFIDENCE} - under a "
                 f"constrained budget, reallocate up to {candidate_share:.0%} of its allocation to "
                 f"higher-lift candidates instead of extending."),
                max(_boundary_confidence(health), 0.7),
                {"action": "REALLOCATE_BUDGET", "reallocation_candidate_share": round(float(candidate_share), 2)},
            )
    return None



def _build_output(signal: StoreSignal, health_score: float, recovery_pct: float,
                   recommendation: str, confidence: float, reason: str, requires_approval: bool,
                   outcome_context: dict | None = None,
                   diversification: dict | None = None,
                   scale_up_eligible: bool | None = None,
                   recovery_velocity: float | None = None) -> dict:
    output = {
        "store_id": signal.store_id,
        "recommendation": recommendation,
        "confidence": confidence,
        "reason": reason,
        "store_health_score": health_score,
        "recovery_pct": round(recovery_pct, 1),
        # Signed momentum is surfaced explicitly (the health score clamps
        # negatives at 0, so its recovery component cannot distinguish a
        # declining store from a merely flat one - see _recovery_direction).
        "recovery_direction": _recovery_direction(recovery_pct),
        "recovery_velocity": round(recovery_velocity, 4) if recovery_velocity is not None else None,
        "days_remaining": signal.days_remaining,
        "requires_human_approval": requires_approval,
        "causal_baseline": dict(CAUSAL_BASELINE),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if outcome_context is not None:
        output["outcome_evidence"] = outcome_context
    if scale_up_eligible is not None:
        output["scale_up_eligible"] = scale_up_eligible
    if diversification is not None:
        output["diversification"] = diversification
    return output
