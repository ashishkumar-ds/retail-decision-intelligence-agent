# Scorer - computes recovery_pct, recovery_velocity, health_score, and
# applies the decision rule chain that produces a recommendation.
from dataclasses import dataclass
from datetime import datetime, timezone

from guardrails import requires_human_approval


@dataclass
class StoreSignal:
    store_id: int
    baseline_forecast: float
    current_forecast: float
    days_elapsed: int
    days_remaining: int
    forecast_signal_available: bool


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
    # hand) - not a calibrated probability of recovery.
    recovery_component = min(max(recovery_pct / 30.0, 0.0), 1.0) * 50
    velocity_component = min(max(recovery_velocity / 1.0, 0.0), 1.0) * 30
    completeness_component = 20.0 if signal_available else 0.0
    return round(recovery_component + velocity_component + completeness_component, 1)


def _boundary_confidence(health_score: float) -> float:
    """Lower confidence the closer the score sits to a decision boundary (40 or 70)."""
    distances = [abs(health_score - b) for b in (40, 70)]
    nearest = min(distances)
    return round(min(0.5 + nearest / 40, 1.0), 2)


def no_data_recommendation(signal: StoreSignal) -> dict:
    # Terminal case for the "no_data" route - skips recovery/velocity/
    # health computation entirely since there's no data to compute it from.
    return _build_output(
        signal, health_score=0.0, recovery_pct=0.0,
        recommendation="NEEDS_REVIEW", confidence=1.0,
        reason=f"No forecast signal available for store {signal.store_id} - cannot evaluate without data.",
        requires_approval=requires_human_approval("NEEDS_REVIEW"),
    )


def score_and_recommend(signal: StoreSignal) -> dict:
    # Decision rules applied in order; first match wins.
    if not signal.forecast_signal_available:
        return no_data_recommendation(signal)

    recovery_pct = compute_recovery_pct(signal)
    velocity = compute_recovery_velocity(recovery_pct, signal.days_elapsed)
    health = compute_health_score(recovery_pct, velocity, signal.forecast_signal_available)

    if health >= 70:
        rec = "CONTINUE"
        reason = f"Store {signal.store_id} health score {health} (recovery {recovery_pct:.1f}%) - on track, no action needed."
    elif health < 40 and signal.days_remaining < 14:
        rec = "ESCALATE"
        reason = (f"Store {signal.store_id} health score {health} with only {signal.days_remaining} days "
                   f"remaining - unlikely to hit the 2-month target without intervention.")
    elif health < 40:
        rec = "EXTEND_INTERVENTION"
        reason = f"Store {signal.store_id} health score {health} - underperforming, but {signal.days_remaining} days remain to recover."
    elif velocity <= 0 and signal.days_remaining < 30:
        rec = "ESCALATE"
        reason = f"Store {signal.store_id} recovery has stalled (velocity {velocity:.2f}%/day) with {signal.days_remaining} days left."
    else:
        rec = "MONITOR"
        reason = f"Store {signal.store_id} health score {health} - middling signal, continue watching."

    confidence = _boundary_confidence(health)

    return _build_output(
        signal, health_score=health, recovery_pct=recovery_pct,
        recommendation=rec, confidence=confidence, reason=reason,
        requires_approval=requires_human_approval(rec),
    )


def _build_output(signal: StoreSignal, health_score: float, recovery_pct: float,
                   recommendation: str, confidence: float, reason: str, requires_approval: bool) -> dict:
    return {
        "store_id": signal.store_id,
        "recommendation": recommendation,
        "confidence": confidence,
        "reason": reason,
        "store_health_score": health_score,
        "recovery_pct": round(recovery_pct, 1),
        "days_remaining": signal.days_remaining,
        "requires_human_approval": requires_approval,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
