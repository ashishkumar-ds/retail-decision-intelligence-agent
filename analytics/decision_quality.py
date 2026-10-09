"""Decision-quality metrics over the system of record (analytics/decision_quality.py).

Technical metrics (latency, eval pass rates) say the machine runs; these say
the *decisions* are good. All inputs are persisted records — the
recommendation log (``memory/history.py``), the approval ledger
(``approvals/ledger.py``) and the execution journal (``execution/journal.py``)
— so every figure is recomputable from the audit trail:

- ``recommendation_acceptance_rate``: approved / decided gated recommendations
- ``causal_confirmation_rate``: CONFIRMED / causally-resolved outcomes
- ``false_intervention_rate``: NEGATIVE / SUFFICIENT outcomes (we acted, sales fell)
- ``reversal_rate``: reversed / executed actions
- ``incremental_margin_per_intervention``: mean realised margin over SUFFICIENT
  outcomes (uplift × baseline × margin rate, 14-day recent window)
- ``regret``: per store, ``max(0, 0 - realised_margin)`` — money lost versus
  the do-nothing counterfactual. Cross-action regret with learned lifts belongs
  to the offline policy comparison (``decision_engine/policy_v2.py``), not here.

Pure functions, never raise for absent data (empty inputs give nulls and zero
counts). Per-store margin overrides come from ``tools/retail_context.py`` when
the caller supplies them; otherwise the flat default applies.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

DEFAULT_MARGIN_RATE = 0.25
RECENT_WINDOW_DAYS = 14
PRIOR_LIFT_PCT = 2.84
EVALUATION_WINDOW_DAYS = 60  # same window as phase2/evaluator + budget_allocator


def projected_portfolio_margin(
    recommendations: Sequence[Mapping[str, Any]],
    *,
    margin_rate: float = DEFAULT_MARGIN_RATE,
    store_margins: Mapping[int, float] | None = None,
    prior_lift_pct: float = PRIOR_LIFT_PCT,
    evaluation_window_days: int = EVALUATION_WINDOW_DAYS,
    baseline_overrides: Mapping[int, float] | None = None,
) -> dict[str, Any]:
    """Honest prior projection over the FULL store panel (uses all dataset).

    Baseline precedence per store:
    1. ``outcome_evidence.baseline_value`` — the evaluator's measured 56-day
       baseline (``baseline_source="outcome"``);
    2. ``baseline_overrides[store_id]`` — an observed daily-mean sales figure
       the caller derived from the actuals feed (``baseline_source="actuals"``);
    3. neither → the store lands in ``unprojected`` (never invented).

    Projects ``baseline * 60d * 2.84% * store margin`` — the Part 1 DiD prior,
    a PLANNING figure (same formula as ``phase2/budget_allocator.py`` and
    ``simulator.compare_candidate_actions``), never a realised one: measured
    money lives in ``compute_decision_quality`` only. Pure function, never
    raises; overrides are caller-supplied data, not fetched here.
    """
    store_margins = store_margins or {}
    overrides = dict(baseline_overrides or {})
    per_store: list[dict[str, Any]] = []
    unprojected: list[int] = []
    for record in recommendations:
        if not isinstance(record, Mapping):
            continue
        sid = record.get("store_id")
        if isinstance(sid, bool) or not isinstance(sid, int):
            continue
        baseline = None
        source = "outcome"
        outcome = record.get("outcome_evidence")
        if isinstance(outcome, Mapping):
            candidate = outcome.get("baseline_value")
            if (not isinstance(candidate, bool) and isinstance(candidate, (int, float))
                    and candidate > 0):
                baseline = float(candidate)
        if baseline is None:
            override = overrides.get(sid)
            if (not isinstance(override, bool) and isinstance(override, (int, float))
                    and override > 0):
                baseline, source = float(override), "actuals"
        rate = store_margins.get(sid, margin_rate)
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            rate = margin_rate
        if baseline is None:
            unprojected.append(sid)
            continue
        projected = baseline * evaluation_window_days * float(prior_lift_pct) / 100.0 * float(rate)
        per_store.append({"store_id": sid, "baseline_daily_sales": round(baseline, 2),
                          "baseline_source": source,
                          "margin_rate": round(float(rate), 4),
                          "projected_margin": round(projected, 2)})
    total = round(sum(p["projected_margin"] for p in per_store), 2)
    return {
        "projected_total_margin": total,
        "projected_store_count": len(per_store),
        "unprojected_store_ids": sorted(set(unprojected)),
        "per_store": sorted(per_store, key=lambda p: -p["projected_margin"]),
        "methodology": {
            "formula": (f"baseline_daily_sales * {evaluation_window_days}d * "
                        f"{prior_lift_pct}% * store margin_rate"),
            "baseline": "evaluated 56d baseline when measured, else observed actuals mean",
            "prior": f"Part 1 DiD +{prior_lift_pct}% (projection, NOT measured)",
            "warning": "do not add to realised margin; realised lives in compute_decision_quality",
        },
    }

_RESOLVED_CAUSAL = frozenset({"CONFIRMED", "REVIEW_ZONE", "REFUTED"})


def _is_measured(outcome: Mapping[str, Any]) -> bool:
    """SUFFICIENT evidence; pre-baseline-anchor records with a numeric uplift count too."""
    if outcome.get("evidence_state") == "SUFFICIENT":
        return True
    if outcome.get("evidence_state") is None:
        uplift = outcome.get("actual_uplift_pct")
        return isinstance(uplift, (int, float)) and not isinstance(uplift, bool)
    return False


def _is_gated(record: Mapping[str, Any]) -> bool:
    from guardrails import requires_human_approval

    return bool(requires_human_approval(str(record.get("recommendation", ""))))


def _latest_per_store(recommendations: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    latest: dict[int, Mapping[str, Any]] = {}
    for record in recommendations:
        sid = record.get("store_id")
        if isinstance(sid, bool) or not isinstance(sid, int):
            continue
        latest[sid] = record
    return latest


def _realised_margin(
    outcome_evidence: Mapping[str, Any],
    margin_rate: float,
    recent_window_days: int = RECENT_WINDOW_DAYS,
) -> float | None:
    """Realised incremental margin for one measured outcome, or None."""
    if not _is_measured(outcome_evidence):
        return None
    uplift = outcome_evidence.get("actual_uplift_pct")
    baseline = outcome_evidence.get("baseline_value")
    if (isinstance(uplift, bool) or not isinstance(uplift, (int, float))
            or isinstance(baseline, bool) or not isinstance(baseline, (int, float))):
        return None
    if baseline <= 0:
        return None
    return float(uplift) / 100.0 * float(baseline) * recent_window_days * float(margin_rate)


def compute_decision_quality(
    recommendations: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    executions: Sequence[Mapping[str, Any]],
    *,
    margin_rate: float = DEFAULT_MARGIN_RATE,
    store_margins: Mapping[int, float] | None = None,
    recent_window_days: int = RECENT_WINDOW_DAYS,
) -> dict[str, Any]:
    """Aggregate decision-quality metrics. Never raises for absent data."""
    recommendations = [r for r in recommendations if isinstance(r, Mapping)]
    decisions = [d for d in decisions if isinstance(d, Mapping)]
    executions = [e for e in executions if isinstance(e, Mapping)]
    store_margins = store_margins or {}

    latest = _latest_per_store(recommendations)
    gated_stores = {sid for sid, rec in latest.items() if _is_gated(rec)}
    decided = {str(d.get("recommendation_id")): d for d in decisions
               if d.get("recommendation_id") is not None}
    decided_gated = [sid for sid in gated_stores
                     if str(latest[sid].get("recommendation_id")) in decided]
    approvals = [sid for sid in decided_gated
                 if decided[str(latest[sid].get("recommendation_id"))].get("decision") == "approve"]
    acceptance_rate = (len(approvals) / len(decided_gated)) if decided_gated else None

    sufficient = [r.get("outcome_evidence") for r in recommendations
                  if isinstance(r.get("outcome_evidence"), Mapping)
                  and _is_measured(r["outcome_evidence"])]
    negatives = sum(1 for o in sufficient if o.get("target_assessment") == "NEGATIVE")
    false_intervention_rate = (negatives / len(sufficient)) if sufficient else None

    causal_states = [o["causal_evidence"]["assessment_state"]
                     for o in sufficient
                     if isinstance(o.get("causal_evidence"), Mapping)
                     and o["causal_evidence"].get("assessment_state") in _RESOLVED_CAUSAL]
    confirmed = sum(1 for s in causal_states if s == "CONFIRMED")
    causal_confirmation_rate = (confirmed / len(causal_states)) if causal_states else None

    execution_events = [e for e in executions if e.get("event") == "execution"]
    reversed_count = sum(1 for e in execution_events if e.get("reversed_at"))
    reversal_rate = (reversed_count / len(execution_events)) if execution_events else None

    margins: list[float] = []
    regrets: dict[int, float] = {}
    for record in recommendations:
        outcome = record.get("outcome_evidence")
        if not isinstance(outcome, Mapping):
            continue
        sid = record.get("store_id")
        rate = store_margins.get(sid, margin_rate) if isinstance(sid, int) else margin_rate
        realised = _realised_margin(outcome, rate, recent_window_days)
        if realised is None:
            continue
        margins.append(realised)
        if isinstance(sid, int) and realised < 0:
            regrets[sid] = round(-realised, 2)

    return {
        "recommendation_acceptance_rate": acceptance_rate,
        "decided_gated_count": len(decided_gated),
        "approval_count": len(approvals),
        "causal_confirmation_rate": causal_confirmation_rate,
        "causally_resolved_count": len(causal_states),
        "false_intervention_rate": false_intervention_rate,
        "sufficient_outcome_count": len(sufficient),
        "reversal_rate": reversal_rate,
        "execution_count": len(execution_events),
        "reversed_count": reversed_count,
        "incremental_margin_per_intervention": (
            round(sum(margins) / len(margins), 2) if margins else None
        ),
        "total_incremental_margin": round(sum(margins), 2) if margins else 0.0,
        "measured_intervention_count": len(margins),
        "mean_regret_vs_do_nothing": (
            round(sum(regrets.values()) / len(regrets), 2) if regrets else None
        ),
        "total_regret_vs_do_nothing": round(sum(regrets.values()), 2),
        "stores_with_regret": sorted(regrets),
        "methodology": {
            "acceptance": "ledger approvals / decided gated recommendations (latest per store)",
            "causal_confirmation": "CONFIRMED / causally-resolved (CONFIRMED+REVIEW_ZONE+REFUTED) outcomes",
            "false_intervention": "NEGATIVE target_assessment / SUFFICIENT outcomes",
            "reversal": "journal executions with reversed_at / all executions",
            "margin": (f"actual_uplift_pct/100 * baseline_value * {recent_window_days}d "
                        "* store margin_rate (override or default)"),
            "regret": "per store max(0, 0 - realised_margin); cross-action regret lives in policy_v2 eval",
        },
    }
