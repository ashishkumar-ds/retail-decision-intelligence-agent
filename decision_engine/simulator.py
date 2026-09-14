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

    coverage_days = len(in_window)
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
