"""Health/confidence reliability - do the scores rank stores the way later
measurements confirm? (analytics/reliability.py)

``compute_decision_quality`` asks whether the *decisions* made money; this
module asks the upstream question the external-review hardening list called
"reliability of health & confidence": recorded scores versus later measured
outcomes.

The circularity trap that shapes everything here: ``decision_engine/scorer``
ingests ``outcome_evidence`` as an *input* (``_apply_outcome_feedback`` runs
before the record is written), so pairing a record's health/confidence with
the uplift sitting in the same record re-uses the measurement to score itself.
Pairing is therefore TIME-LAGGED: a record scored at time t only pairs with
the first *measured* phase-2 ``evaluate`` event of the same store whose
``occurred_at`` is at least ``min_lag_days`` later. The lag approximates one
56-day DiD cycle when the data allows it (the offline demo recordings span 21
days, so pass ``--min-lag-days 0`` there and accept the verdict that says so).

What is implemented - and the honesty boundary it carries (stdlib only):

- **Discrimination, not calibration.** ``spearman_rank_correlation`` (average
  ranks, tie-corrected) of ``store_health_score`` and ``confidence`` against
  later measured ``actual_uplift_pct``. Both scores are ranking devices *by
  construction* (health is a recomputable composite; confidence is derived
  from decision-boundary distance), so an isotonic/Platt fit or a
  predicted-vs-observed reliability diagram would dress them up as
  probabilities they have never claimed to be. Directional agreement first.
- **Binned directional table** - mean measured uplift and positive-outcome
  share per ``store_health_score`` bin.
- **Verdict gating** - below ``DIRECTIONAL_AT`` pairs the report says
  ``insufficient_evidence`` instead of flattering small numbers.

Same input discipline as ``analytics/decision_quality.py``: pure functions
over persisted records; never raises for absent or malformed rows (they are
skipped, and the counts say how many survived).
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

# Half a 56-day DiD cycle: shorter lags risk pairing a score with an outcome
# whose measurement window overlaps the decision (partial circularity).
DEFAULT_MIN_LAG_DAYS = 28
# Verdict gating: below these pair counts the report downgrades itself.
DIRECTIONAL_AT = 8    # < this -> insufficient_evidence (cannot rank-check)
MEASURED_AT = 30      # >= this -> measured (stable-ish sample)
HEALTH_BIN_EDGES = (0.0, 20.0, 40.0, 60.0, 80.0, 100.0)


def _parse_moment(value: Any) -> datetime | None:
    """Parse an ISO timestamp or pass through a datetime; naive becomes UTC."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return float(value)


def _measured_outcome(row: Mapping[str, Any]) -> tuple[int, datetime, float, Any] | None:
    """(store_id, occurred_at, uplift, target_assessment) from one outcome row.

    Tolerates both shapes the audit trail produces: flat dicts
    (``store_id``/``actual_uplift_pct`` at top level, as stored on
    recommendation records) and phase-2 registry records (ids in ``key``,
    measurements in ``payload``). Returns None when any required piece is
    missing, malformed or non-finite; callers skip None without raising.
    """
    sid = row.get("store_id")
    if isinstance(sid, bool) or not isinstance(sid, int):
        key = row.get("key") if isinstance(row.get("key"), Mapping) else {}
        sid = key.get("store_id")
        if isinstance(sid, bool) or not isinstance(sid, int):
            return None
    moment = _parse_moment(row.get("occurred_at"))
    if moment is None:
        return None
    uplift = _numeric(row.get("actual_uplift_pct"))
    payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else None
    if uplift is None and payload is not None:
        uplift = _numeric(payload.get("actual_uplift_pct"))
    if uplift is None:
        return None
    target = row.get("target_assessment")
    if target is None and payload is not None:
        target = payload.get("target_assessment")
    return sid, moment, float(uplift), target


def evaluate_outcome_events(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Measured outcomes out of phase-2 registry records (the lag-join source).

    Accepts ``InterventionEvent.to_record()`` dicts: only ``evaluate`` events
    carrying a finite numeric ``payload.actual_uplift_pct`` count as
    measured - everything else is skipped (``evidence_state`` is not required
    here; older events predate the field). Returns ascending-store dicts of
    {store_id, occurred_at, actual_uplift_pct, target_assessment,
    intervention_id, outcome_id}.
    """
    measured: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping) or record.get("event_type") != "evaluate":
            continue
        extracted = _measured_outcome(record)
        if extracted is None:
            continue
        sid, moment, uplift, target = extracted
        payload = record.get("payload") if isinstance(record.get("payload"), Mapping) else {}
        measured.append({
            "store_id": sid,
            "occurred_at": moment.isoformat(),
            "actual_uplift_pct": uplift,
            "target_assessment": target,
            "intervention_id": record.get("intervention_id"),
            "outcome_id": payload.get("outcome_id"),
        })
    return measured


def _rank(values: list[float]) -> list[float]:
    """Average ranks, ties shared (1-based)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def spearman_rank_correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Tie-corrected Spearman rho (Pearson over average ranks), or None.

    None when fewer than 2 usable pairs or when either side is constant -
    a constant input has no rank spread, and pretending a correlation of 0
    "measured nothing" would be the exact vibes this module exists to prevent.
    """
    n = min(len(xs), len(ys))
    if n < 2:
        return None
    rx = _rank(list(xs)[:n])
    ry = _rank(list(ys)[:n])
    if len(set(rx)) == 1 or len(set(ry)) == 1:
        return None
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    cov = sum((a - mean_rx) * (b - mean_ry) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mean_rx) ** 2 for a in rx) * sum((b - mean_ry) ** 2 for b in ry))
    if den <= 0:
        return None
    return cov / den


def spearman_p_value(rho: float, n: int) -> float | None:
    """Two-sided p for Spearman rho under the normal approximation.

    ``z = |rho| * sqrt(n - 1)``, p ``= erfc(z / sqrt(2))``. Honest labels:
    the approximation is only indicative for small n, and callers surface the
    ``n`` that produced it - nothing here pretends an n=4 p-value is evidence.
    A perfect |rho|=1 with n >= 3 reports p=0.0 by construction, not by test.
    """
    if n < 3:
        return None
    if abs(rho) >= 1.0:
        return 0.0
    z = abs(rho) * math.sqrt(float(n - 1))
    return max(0.0, min(1.0, math.erfc(z / math.sqrt(2.0))))


def lagged_score_outcome_pairs(
    recommendations: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    min_lag_days: float = DEFAULT_MIN_LAG_DAYS,
) -> list[dict[str, Any]]:
    """Time-lagged (score, measured outcome) pairs - the non-circular join.

    For each recommendation record (``store_id``, ``generated_at``,
    ``store_health_score``, ``confidence``), the first measured outcome of the
    same store occurring at least ``min_lag_days`` later. Same-record outcomes
    are never paired (circularity); malformed rows on either side are skipped.
    Returns dicts with store_id, generated_at, lag_days, health, confidence,
    actual_uplift_pct, target_assessment.
    """
    by_store: dict[int, list[tuple[datetime, Mapping[str, Any], float, Any]]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            continue
        measured = _measured_outcome(outcome)
        if measured is None:
            continue
        sid, moment, uplift, target = measured
        by_store.setdefault(sid, []).append((moment, outcome, uplift, target))
    for sid in by_store:
        by_store[sid].sort(key=lambda item: item[0])

    pairs: list[dict[str, Any]] = []
    for record in recommendations:
        if not isinstance(record, Mapping):
            continue
        sid = record.get("store_id")
        if isinstance(sid, bool) or not isinstance(sid, int):
            continue
        health = _numeric(record.get("store_health_score"))
        confidence = _numeric(record.get("confidence"))
        scored_at = _parse_moment(record.get("generated_at"))
        if health is None or confidence is None or scored_at is None:
            continue
        for moment, outcome, uplift, target in by_store.get(sid, ()):
            # Strictly-after guard stands regardless of min_lag_days: an
            # evaluate event stamped at the score's own instant is the
            # tautological same-record join, whatever the caller's lag is.
            if moment <= scored_at:
                continue
            lag_days = (moment - scored_at).total_seconds() / 86400.0
            if lag_days >= min_lag_days:
                pairs.append({
                    "store_id": sid,
                    "generated_at": scored_at.isoformat(),
                    "lag_days": round(lag_days, 2),
                    "health": health,
                    "confidence": confidence,
                    "actual_uplift_pct": float(uplift),
                    "target_assessment": target,
                })
                break
    return pairs


def _spearman_entry(pairs, key: str) -> dict[str, Any]:
    xs = [pair["health"] if key == "health" else pair["confidence"] for pair in pairs]
    ys = [pair["actual_uplift_pct"] for pair in pairs]
    rho = spearman_rank_correlation(xs, ys)
    n = len(pairs)
    p_value = spearman_p_value(rho, n) if rho is not None else None
    notes = []
    if rho is None:
        notes.append("undefined - constant side or too few pairs")
    if key == "confidence":
        notes.append("confidence is decision-boundary decisiveness "
                     "(decision_engine/scorer._boundary_confidence), not a success probability")
    return {"rho": None if rho is None else round(rho, 4),
            "p_value": None if p_value is None else round(p_value, 4),
            "n": n,
            "note": "; ".join(notes) if notes else None}


def health_reliability_bins(pairs: Sequence[Mapping[str, Any]],
                            edges: Sequence[float] = HEALTH_BIN_EDGES,
                            ) -> list[dict[str, Any]]:
    """Binned directional table over store_health_score (the rank-check view).

    One row per health bin: measured-outcome count, mean measured uplift and
    the positive-outcome share inside the bin. Bin bounds stay recomputable:
    lower-inclusive, upper-exclusive, last bin closed at the top edge.
    """
    binned: list[dict[str, Any]] = []
    for index in range(len(edges) - 1):
        lo, hi = edges[index], edges[index + 1]
        inside = [pair for pair in pairs
                  if lo <= pair["health"] < hi or (index == len(edges) - 2 and pair["health"] == hi)]
        uplifts = [pair["actual_uplift_pct"] for pair in inside]
        positives = sum(1 for uplift in uplifts if uplift > 0)
        binned.append({
            "bin": f"{lo:g}-{hi:g}",
            "measured_count": len(inside),
            "mean_measured_uplift_pct": (
                round(statistics.fmean(uplifts), 2) if uplifts else None),
            "positive_share": round(positives / len(inside), 3) if inside else None,
        })
    return binned


def _verdict(paired_count: int) -> dict[str, str]:
    if paired_count == 0:
        return {"verdict": "no_lagged_measured_outcomes",
                "verdict_note": ("no (score, later measured outcome) pairs - seed the demo, "
                                 "run a sweep, and let interventions earn evaluate events")}
    if paired_count < DIRECTIONAL_AT:
        return {"verdict": "insufficient_evidence",
                "verdict_note": (f"{paired_count} paired outcomes is too few to rank-check "
                                 f"(below {DIRECTIONAL_AT}); the correlation columns say "
                                 "nothing reliable yet")}
    if paired_count < MEASURED_AT:
        return {"verdict": "directional_only",
                "verdict_note": (f"{paired_count} pairs - directional signal only; "
                                 f"rank-check below is indicative, not inferential")}
    return {"verdict": "measured",
            "verdict_note": (f"{paired_count} pairs - enough to read the rank-check "
                             "(still observational, not causal)")}


def compute_health_reliability(
    recommendations: Sequence[Mapping[str, Any]],
    outcome_records: Sequence[Mapping[str, Any]],
    *,
    min_lag_days: float = DEFAULT_MIN_LAG_DAYS,
) -> dict[str, Any]:
    """Aggregate health/confidence reliability. Never raises for absent data.

    ``recommendations`` are recommendation-log records; ``outcome_records``
    are phase-2 registry records (raw ``to_record()`` dicts or the output of
    :func:`evaluate_outcome_events`). The lag-join, bins, rank correlations
    and verdict are described in :data:`methodology` in the return value.
    """
    outcomes = evaluate_outcome_events(outcome_records)
    pairs = lagged_score_outcome_pairs(recommendations, outcomes, min_lag_days)
    verdict = _verdict(len(pairs))
    lags = [pair["lag_days"] for pair in pairs]
    return {
        "paired_outcome_count": len(pairs),
        "measured_outcome_events": len(outcomes),
        "min_lag_days": min_lag_days,
        "lag_days": ({
            "mean": round(statistics.fmean(lags), 2),
            "min": min(lags),
            "max": max(lags),
        } if lags else None),
        "spearman": {
            "health_vs_measured_uplift": _spearman_entry(pairs, "health"),
            "confidence_vs_measured_uplift": _spearman_entry(pairs, "confidence"),
        },
        "health_bins": health_reliability_bins(pairs),
        "directional_hit_rate": (
            round(sum(1 for pair in pairs if pair["actual_uplift_pct"] > 0) / len(pairs), 3)
            if pairs else None),
        **verdict,
        "methodology": {
            "pairing": ("time-lagged: record scored at t pairs with the same store's first "
                        "phase-2 evaluate event measured at >= t + min_lag_days"),
            "non_circular": ("same-record outcome_evidence is never paired - it is an input "
                             "to scoring (decision_engine/scorer._apply_outcome_feedback), so "
                             "same-record agreement would be tautological"),
            "health": ("engine composite (recovery + velocity + completeness), "
                       "recomputable by hand - a rank device, not a probability"),
            "confidence": ("boundary-distance decisiveness (closer to the 40/70 health "
                           "thresholds means lower), tempered by outcome/causal rules"),
            "discrimination_not_calibration": ("Spearman rho reports directional agreement; "
                                               "isotonic/Platt calibration or a reliability "
                                               "diagram needs predicted probabilities and far "
                                               "more measured outcomes than exists today"),
            "p_value": ("two-sided, normal approximation z=|rho|*sqrt(n-1); only "
                        "indicative at n<10 and gated by the verdict field"),
        },
    }
