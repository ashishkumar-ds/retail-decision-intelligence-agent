"""
Deterministic recommendation engine.

This module contains zero LLM calls and zero calls to an external API by
itself - it's pure functions over numbers, on purpose. The recommendation
an LLM narrates later is decided entirely here, so the decision logic can
be unit-tested and audited independently of anything an LLM does.
"""
from dataclasses import dataclass
from datetime import datetime, timezone


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
    # Recovery component: capped at 100% of a 30% recovery target, so hitting
    # or exceeding target doesn't produce a runaway score past 100.
    recovery_component = min(max(recovery_pct / 30.0, 0.0), 1.0) * 50

    # Velocity component: normalized against 1%/day as a reasonable pace;
    # negative velocity contributes zero, not a negative score.
    velocity_component = min(max(recovery_velocity / 1.0, 0.0), 1.0) * 30

    completeness_component = 20.0 if signal_available else 0.0

    return round(recovery_component + velocity_component + completeness_component, 1)


def recommend(signal: StoreSignal) -> dict:
    """
    Applies the Task 4 decision rules in order; first match wins.
    Returns a dict matching the Task 9 output schema exactly.
    """
    if not signal.forecast_signal_available:
        return _build_output(
            signal, health_score=0.0, recovery_pct=0.0,
            recommendation="NEEDS_REVIEW", confidence=1.0,
            reason=f"No forecast signal available for store {signal.store_id} - cannot evaluate without data.",
            requires_approval=True,
        )

    recovery_pct = compute_recovery_pct(signal)
    velocity = compute_recovery_velocity(recovery_pct, signal.days_elapsed)
    health = compute_health_score(recovery_pct, velocity, signal.forecast_signal_available)

    if health >= 70:
        rec, requires_approval = "CONTINUE", False
        reason = f"Store {signal.store_id} health score {health} (recovery {recovery_pct:.1f}%) - on track, no action needed."
    elif health < 40 and signal.days_remaining < 14:
        rec, requires_approval = "ESCALATE", True
        reason = (f"Store {signal.store_id} health score {health} with only {signal.days_remaining} days "
                   f"remaining - unlikely to hit the 2-month target without intervention.")
    elif health < 40:
        rec, requires_approval = "EXTEND_INTERVENTION", True
        reason = f"Store {signal.store_id} health score {health} - underperforming, but {signal.days_remaining} days remain to recover."
    elif velocity <= 0 and signal.days_remaining < 30:
        rec, requires_approval = "ESCALATE", True
        reason = f"Store {signal.store_id} recovery has stalled (velocity {velocity:.2f}%/day) with {signal.days_remaining} days left."
    else:
        rec, requires_approval = "MONITOR", False
        reason = f"Store {signal.store_id} health score {health} - middling signal, continue watching."

    confidence = _boundary_confidence(health)

    return _build_output(
        signal, health_score=health, recovery_pct=recovery_pct,
        recommendation=rec, confidence=confidence, reason=reason,
        requires_approval=requires_approval,
    )


def _boundary_confidence(health_score: float) -> float:
    """Lower confidence the closer the score sits to a decision boundary (40 or 70)."""
    distances = [abs(health_score - b) for b in (40, 70)]
    nearest = min(distances)
    return round(min(0.5 + nearest / 40, 1.0), 2)


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
