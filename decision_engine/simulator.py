"""Pre-approval intervention simulator (backtest before action).

Answers, BEFORE a Store Manager approves a budget-affecting intervention:
"If this candidate starts at day S, what does the calibrated causal prior
project, and would that projection clear the causal guardrail?"

Design contract (mirrors the repo):

- Deterministic: same observations + calibration -> same envelope. No RNG,
  no model calls, no wall-clock inputs.
- Recomputable: the projection applies the Part 1 DiD calibration prior
  (decision_engine.calibration.CAUSAL_BASELINE) to the store's own observed
  baseline from ``tools.get_actuals``. The prior is already
  treated-minus-control, so control drift is netted out by construction.
- Fail-closed: insufficient baseline coverage or degenerate sales return
  ``evidence_state: INSUFFICIENT`` with the reason. The simulator never
  invents sales data - coverage gaps are evidence (per ``tools.get_actuals``).
- Non-causal signals are labeled and walled off: the store's own-baseline
  momentum is reported separately and NEVER enters the causal projection
  (own-baseline lift silently credits market drift - see
  decision_engine.causality).

What this is NOT: a prediction of what will happen. It is the projection a
human approver should hold in mind - point estimate, uncertainty band, and
the guardrail verdict that projection would earn - while the real effect is
only knowable from the matched-control DiD after the window closes.
"""
from __future__ import annotations

from statistics import fmean
from typing import Any, Mapping, Sequence

from decision_engine.calibration import CAUSAL_BASELINE
from decision_engine.causality import assess_causal_evidence
from guardrails import risk_of

# Baseline evidence floor: at least this fraction of the pre-window days must
# have observations, or the projection is INSUFFICIENT (fail-closed).
MIN_BASELINE_COVERAGE_RATIO = 0.75

# Own-baseline momentum needs this many observed days per half-window.
MIN_MOMENTUM_HALF_COVERAGE = 14

_LIMITATIONS = [
    "The projection applies the Part 1 DiD calibration prior; it is not a "
    "store-specific causal prediction.",
    "The realized effect is only knowable from the matched-control DiD after "
    "the evaluation window closes.",
    "Own-baseline momentum is reported for context and is deliberately "
    "excluded from the causal projection.",
]


def _require_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    return value


def _baseline_stats(
    observations: Sequence[Mapping[str, Any]],
    started_day: int,
    pre_window_days: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """Aggregate the pre-window observations; None + reason when insufficient."""
    if pre_window_days <= 0:
        return None, (
            f"invalid pre-window length {pre_window_days} days "
            f"(must be >= 1 to compute baseline coverage)"
        )
    window_start = started_day - pre_window_days
    window_end = started_day - 1
    in_window: list[tuple[int, float]] = []
    for obs in observations:
        if not isinstance(obs, Mapping):
            raise TypeError("each observation must be a mapping with 'day' and 'sales_value'")
        day, value = obs.get("day"), obs.get("sales_value")
        if isinstance(day, bool) or not isinstance(day, int):
            raise TypeError("observation 'day' must be an integer")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("observation 'sales_value' must be numeric")
        if window_start <= day <= window_end:
            in_window.append((day, float(value)))

    coverage_days = len({day for day, _ in in_window})
    coverage_ratio = coverage_days / pre_window_days
    if coverage_ratio < MIN_BASELINE_COVERAGE_RATIO:
        return None, (
            f"baseline coverage {coverage_days}/{pre_window_days} days "
            f"({coverage_ratio:.0%}) below the "
            f"{MIN_BASELINE_COVERAGE_RATIO:.0%} evidence floor"
        )
    in_window.sort(key=lambda pair: pair[0])
    sales = [value for _, value in in_window]
    baseline_mean = fmean(sales)
    if baseline_mean <= 0:
        return None, "no observed sales in the baseline window"

    # Own-baseline momentum: second half vs first half of the pre-window.
    # Reported for context; deliberately excluded from the causal projection.
    half = pre_window_days // 2
    first = [value for day, value in in_window if day < window_start + half]
    second = [value for day, value in in_window if day >= window_start + half]
    momentum = None
    if (len(first) >= MIN_MOMENTUM_HALF_COVERAGE and len(second) >= MIN_MOMENTUM_HALF_COVERAGE
            and fmean(first) > 0):
        momentum = (fmean(second) / fmean(first) - 1.0) * 100.0

    return {
        "mean_daily_sales": baseline_mean,
        "coverage_days": coverage_days,
        "coverage_ratio": coverage_ratio,
        "own_momentum_pct": momentum,
    }, None


# --- Multi-action comparison (approval choice set) ---------------------------
# All spend actions share the same calibrated causal prior until the caller
# supplies action-specific evidence via ``expected_lifts`` (e.g. learned lifts
# from decision_engine.policy_v2 or measured outcome lifts). Differentiation
# therefore comes from cost, margin and reversibility — never invented lift.

#: Actions the comparison can rank. Do-nothing actions carry lift 0 / cost 0.
COMPARABLE_ACTIONS = (
    "CONTINUE",
    "EXTEND_INTERVENTION",
    "RETARGET_SEGMENT",
    "TIMING_SHIFT",
    "PAUSE_INTERVENTION",
)

_DO_NOTHING_ACTIONS = frozenset({"CONTINUE", "PAUSE_INTERVENTION"})

#: Projection confidence by guardrail state. A stated mapping, not a calibrated
#: probability — the prior CI is what carries the real uncertainty.
GUARDRAIL_CONFIDENCE = {
    "CONFIRMED": 0.8,
    "REVIEW_ZONE": 0.5,
    "REFUTED": 0.3,
    "UNAVAILABLE": 0.3,
}

#: Reversibility rank for deterministic tie-breaks (lower = easier to undo).
_RISK_RANK = {"reversible": 0, "cautious": 1, "irreversible": 2}


def compare_candidate_actions(
    store_id: int,
    observations: Sequence[Mapping[str, Any]],
    started_day: int,
    *,
    pre_window_days: int,
    evaluation_window_days: int,
    prior: Mapping[str, Any] = CAUSAL_BASELINE,
    margin_rate: float = 0.25,
    campaign_costs: Mapping[str, float] | None = None,
    expected_lifts: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Rank candidate actions by expected incremental margin, pre-approval.

    For each action: ``expected_lift_pct`` (prior point for spend actions, 0.0
    for do-nothing, caller override wins) → causal guardrail → incremental
    sales value → ``expected_incremental_margin = base_total * lift/100 *
    margin_rate - campaign_cost`` (do-nothing cost is forced to 0) → risk tier (``guardrails.risk_of``).
    Recommends the highest-margin action; ties break toward the lower-risk,
    then alphabetically-first action (deterministic).

    Fail-closed like :func:`simulate_intervention`: insufficient baseline
    coverage is ``INSUFFICIENT`` with the reason, never a ranking.
    """
    _require_int("store_id", store_id)
    _require_int("started_day", started_day)
    _require_int("pre_window_days", pre_window_days)
    _require_int("evaluation_window_days", evaluation_window_days)
    if pre_window_days < 2 or evaluation_window_days < 1:
        raise ValueError("pre_window_days must be >= 2 and evaluation_window_days >= 1")
    if started_day <= pre_window_days:
        raise ValueError("started_day must exceed pre_window_days (baseline must start at day >= 1)")
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
        raise TypeError("observations must be a sequence of observation mappings")
    if isinstance(margin_rate, bool) or not isinstance(margin_rate, (int, float)):
        raise TypeError("margin_rate must be a number")
    if not 0 < margin_rate <= 1:
        raise ValueError("margin_rate must be in (0, 1]")
    costs = dict(campaign_costs or {})
    lifts = dict(expected_lifts or {})
    for action, cost in costs.items():
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
            raise ValueError(f"campaign cost for {action!r} must be non-negative")
    try:
        prior_point = float(prior["estimate_pct"])
        prior_ci95 = [float(bound) for bound in prior["ci95_pct"]]
        prior_source = prior.get("source", "unspecified")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"prior must provide estimate_pct and ci95_pct: {error}") from error

    baseline, reason = _baseline_stats(observations, started_day, pre_window_days)
    if baseline is None:
        return {
            "store_id": store_id,
            "evidence_state": "INSUFFICIENT",
            "reason": reason,
            "limitations": _LIMITATIONS,
        }
    baseline_mean = baseline["mean_daily_sales"]
    base_total = baseline_mean * evaluation_window_days

    ranked: list[dict[str, Any]] = []
    for action in COMPARABLE_ACTIONS:
        if action in lifts:
            lift = float(lifts[action])
        elif action in _DO_NOTHING_ACTIONS:
            lift = 0.0
        else:
            lift = prior_point
        cost = 0.0 if action in _DO_NOTHING_ACTIONS else float(costs.get(action, 0.0))
        guardrail = assess_causal_evidence(
            {"evidence_state": "SUFFICIENT", "did_uplift_pct": lift}
        )
        state = guardrail["assessment_state"]
        margin = base_total * lift / 100.0 * float(margin_rate) - cost
        ranked.append({
            "action": action,
            "expected_lift_pct": lift,
            "causal_state": state,
            "scale_up_eligible": guardrail["scale_up_eligible"],
            "confidence": GUARDRAIL_CONFIDENCE.get(state, 0.3),
            "risk": risk_of(action),
            "budget_impact": round(cost, 2),
            "expected_incremental_margin": round(margin, 2),
            "incremental_sales_value": round(base_total * lift / 100.0, 2),
            "lift_source": (
                "caller-supplied" if action in lifts
                else ("do-nothing (lift 0 by construction)" if action in _DO_NOTHING_ACTIONS
                      else f"calibrated prior ({prior_source})")
            ),
        })
    ranked.sort(key=lambda row: (-row["expected_incremental_margin"],
                                   _RISK_RANK.get(row["risk"], 9), row["action"]))
    recommended = ranked[0]["action"]
    return {
        "store_id": store_id,
        "evidence_state": "SUFFICIENT",
        "recommended_action": recommended,
        "actions": ranked,
        "baseline": {
            "mean_daily_sales": baseline_mean,
            "coverage_days": baseline["coverage_days"],
            "coverage_ratio": baseline["coverage_ratio"],
        },
        "prior": {"source": prior_source, "point_pct": prior_point, "ci95_pct": prior_ci95},
        "margin_rate": float(margin_rate),
        "verdict": (
            f"Recommended action: {recommended} "
            f"(expected incremental margin "
            f"{ranked[0]['expected_incremental_margin']:.2f} at margin rate "
            f"{float(margin_rate):.2f}). Do-nothing actions carry lift 0 by "
            f"construction; spend actions share the calibrated prior until "
            f"action-specific evidence is supplied."
        ),
        "limitations": _LIMITATIONS,
    }


def simulate_intervention(
    store_id: int,
    observations: Sequence[Mapping[str, Any]],
    started_day: int,
    *,
    pre_window_days: int,
    evaluation_window_days: int,
    prior: Mapping[str, Any] = CAUSAL_BASELINE,
) -> dict[str, Any]:
    """Project a candidate intervention onto the store's observed baseline.

    Args:
        store_id: Store the candidate intervention targets.
        observations: Observed daily sales (``tools.get_actuals`` shape:
            ``{"day", "sales_value"}``); days outside the pre-window are
            ignored, gaps are coverage evidence.
        started_day: Proposed intervention start day.
        pre_window_days: Baseline window length (phase2.evaluator.BASELINE_DAYS).
        evaluation_window_days: Evaluation window length
            (phase2.evaluator.EVALUATION_WINDOW_DAYS).
        prior: Causal prior mapping with ``estimate_pct``, ``ci95_pct``,
            ``source`` (default: the Part 1 DiD calibration).

    Returns:
        The simulation envelope (see module docstring). Never raises for
        business-level evidence gaps - those are INSUFFICIENT, fail-closed.
    """
    _require_int("store_id", store_id)
    _require_int("started_day", started_day)
    _require_int("pre_window_days", pre_window_days)
    _require_int("evaluation_window_days", evaluation_window_days)
    if pre_window_days < 2 or evaluation_window_days < 1:
        raise ValueError("pre_window_days must be >= 2 and evaluation_window_days >= 1")
    if started_day <= pre_window_days:
        raise ValueError("started_day must exceed pre_window_days (baseline must start at day >= 1)")
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
        raise TypeError("observations must be a sequence of observation mappings")
    if not isinstance(prior, Mapping):
        raise TypeError("prior must be a mapping")
    try:
        point_pct = float(prior["estimate_pct"])
        ci95 = [float(bound) for bound in prior["ci95_pct"]]
        prior_source = prior.get("source", "unspecified")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"prior must provide estimate_pct and ci95_pct: {error}") from error

    insufficient_prefix = {
        "store_id": store_id,
        "evidence_state": "INSUFFICIENT",
        "limitations": _LIMITATIONS,
    }

    baseline, reason = _baseline_stats(observations, started_day, pre_window_days)
    if baseline is None:
        return {**insufficient_prefix, "reason": reason}

    momentum = baseline.pop("own_momentum_pct")
    baseline_mean = baseline["mean_daily_sales"]
    base_total = baseline_mean * evaluation_window_days

    guardrail = assess_causal_evidence(
        {"evidence_state": "SUFFICIENT", "did_uplift_pct": point_pct}
    )

    return {
        "store_id": store_id,
        "evidence_state": "SUFFICIENT",
        "simulation": {
            "started_day": started_day,
            "pre_window_days": pre_window_days,
            "evaluation_window_days": evaluation_window_days,
            "baseline": {
                "mean_daily_sales": baseline_mean,
                "coverage_days": baseline["coverage_days"],
                "coverage_ratio": baseline["coverage_ratio"],
            },
            "own_momentum_pct": (
                {"value": momentum, "note": "own-baseline drift; NOT causal"}
                if momentum is not None else None
            ),
            "prior": {"source": prior_source, "point_pct": point_pct, "ci95_pct": ci95},
            "projected": {
                "did_pct": {"point": point_pct, "ci95": ci95},
                "guardrail": guardrail,
                "incremental_value": {
                    "unit": f"sales_value over {evaluation_window_days}-day window",
                    "point": base_total * point_pct / 100.0,
                    "ci95": [base_total * bound / 100.0 for bound in ci95],
                },
            },
        },
        "verdict": (
            f"Point projection lands {guardrail['assessment_state']}; scale-up "
            f"requires realized DiD >= {guardrail['target_pct']}. The CI-95 "
            f"upper band ({ci95[1]}%) is the best case, not the expectation."
        ),
        "limitations": _LIMITATIONS,
    }
