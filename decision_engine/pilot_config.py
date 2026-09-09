"""Pilot-store selection — shared source of truth for testing Campaign 18.

Single home for the 3-store pilot set so the analysis notebook, n8n
execution, and the decision engine all reference ONE definition (previously
the notebook used [299, 317, 448] while the executed n8n run logged
[31642, 317, 299, 289, 31582] — a divergence this module exists to prevent).

Selection methodology (dunnhumby-style, kept simple and auditable):
  1. Store must be a GENUINE UNDERPERFORMER (recovery < 0, health below the
     on-track threshold) so the test measures recovery, not drift of an
     already-healthy store.
  2. Store must have sufficient historical coverage (~56d pre + 56d in-window)
     for a credible baseline and counterfactual.
  3. Picked to span a severity gradient (mild -> severe) so the strategy
     impact is measured across underperformance intensities.
"""
from __future__ import annotations

from typing import Mapping

# The single agreed pilot set (underperforming stores for Campaign 18).
PILOT_STORES: tuple[int, ...] = (317, 299, 289)

ON_TRACK_HEALTH_THRESHOLD = 70.0   # store at/above this is NOT a candidate
MIN_DATA_DAYS = 56                 # minimum pre + in-window data depth

PILOT_METHODOLOGY: dict[str, str] = {
    "objective": "Measure Campaign 18 (Best Customer segment, 12PM-6PM) impact on genuinely underperforming stores",
    "stores": "317, 299, 289",
    "selection": (
        "underperformer recovery<0 AND health<70; data coverage>=56d; "
        "severity gradient: 317 mid(-13.7%), 299 severe(-22.5%), 289 mild(-8.6%)"
    ),
    "excluded": "448, 31582 (already on-track = selection bias); 31642 (redundant with 289)",
    "primary_metric": "causal DiD uplift vs matched controls, NOT raw ML-counterfactual",
}


def validate_pilot_store(
    store_id: int,
    *,
    health_score: float | None,
    recovery_pct: float | None,
    data_days: int | None = None,
) -> list[str]:
    """Return a list of reasons a store is NOT an acceptable underperformer-pilot.

    Empty list means the store passes. This is the enforcer that prevents an
    already-on-track store (e.g. 448) from leaking into the treatment set.
    """
    reasons: list[str] = []
    if health_score is not None:
        if health_score >= ON_TRACK_HEALTH_THRESHOLD:
            reasons.append(f"health {health_score} >= on-track threshold {ON_TRACK_HEALTH_THRESHOLD:.0f} (not underperforming)")
    else:
        reasons.append("no health score available")
    if recovery_pct is not None and recovery_pct >= 0:
        reasons.append(f"recovery {recovery_pct:+.1f}% >= 0 (not below baseline)")
    if data_days is not None and data_days < MIN_DATA_DAYS:
        reasons.append(f"only {data_days} days of data (need >= {MIN_DATA_DAYS})")
    return reasons


def classify_pilot(
    signals: Mapping[int, Mapping[str, float]],
) -> tuple[list[int], dict[int, list[str]]]:
    """Split stores into valid pilots vs rejected, with reasons.

    ``signals``: {store_id: {health_score, recovery_pct, data_days}}.
    """
    accepted: list[int] = []
    rejected: dict[int, list[str]] = {}
    for store_id, signal in signals.items():
        reasons = validate_pilot_store(
            store_id,
            health_score=signal.get("health_score"),
            recovery_pct=signal.get("recovery_pct"),
            data_days=signal.get("data_days"),
        )
        if reasons:
            rejected[store_id] = reasons
        else:
            accepted.append(store_id)
    return sorted(accepted), rejected