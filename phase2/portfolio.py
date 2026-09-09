"""Batch portfolio evaluator for multi-store Phase 2 interventions.

Evaluates an entire store portfolio deterministically:
- Extracts 56-day baseline and 14-day recent observations per store;
- Derives 14-day counterfactual forecast averages via Project 1 Forecast API;
- Evaluates individual store outcomes and validates provenance joins;
- Computes portfolio-level pooled sales, counterfactual lift, and diagnostic summaries;
- Strictly preserves pure evaluator boundaries without side-effects.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import fmean, median
from typing import Any, Mapping, Sequence

import requests

from tools.forecast_tool import (
    ForecastResponseError,
    get_all_stores_info,
    get_evaluation_window_forecast,
    get_store_info,
)

from .contracts import (
    ApprovalRecord,
    InterventionKey,
    InterventionRecord,
    OutcomeEvaluation,
    OutcomeObservation,
    RecommendationRecord,
)
from .evaluator import (
    BASELINE_DAYS,
    EVALUATION_WINDOW_DAYS,
    RECENT_OBSERVATION_DAYS,
    TARGET_UPLIFT_PCT,
    build_intervention_outcome_join,
    build_weekly_checkpoints,
    evaluate_outcome,
)
from .exposure import compute_store_campaign_eligibility

logger = logging.getLogger("retail_decision_agent.portfolio")


@dataclass(frozen=True)
class StoreEvaluationResult:
    store_id: int
    is_eligible: bool
    evidence_state: str
    forecast_status: str
    baseline_daily_mean: float | None
    recent_daily_mean: float | None
    forecast_reference_value: float | None
    longitudinal_uplift_pct: float | None
    counterfactual_uplift_pct: float | None
    recovery_pct_of_target: float | None
    join_state: str
    eligibility_details: dict[str, Any] | None = None
    eligibility_error: str | None = None
    outcome: OutcomeEvaluation | None = None
    error: str | None = None


@dataclass(frozen=True)
class PortfolioEvaluationReport:
    total_stores: int
    eligible_stores_count: int
    sufficient_evidence_count: int
    available_forecast_count: int
    error_count: int
    mean_longitudinal_uplift_pct: float | None
    mean_counterfactual_uplift_pct: float | None
    median_counterfactual_uplift_pct: float | None
    pooled_actual_recent_sales: float
    pooled_counterfactual_forecast_sales: float
    pooled_counterfactual_uplift_pct: float | None
    target_benchmark_pct: float
    store_results: tuple[StoreEvaluationResult, ...]
    methodology: dict[str, str]


def _index_transactions_by_store(
    transactions: Sequence[Mapping[str, Any]],
) -> dict[int, list[Mapping[str, Any]]]:
    tx_by_store: dict[int, list[Mapping[str, Any]]] = {}
    skipped_malformed_tx_count = 0
    for tx in transactions:
        try:
            sid = int(tx.get("STORE_ID", tx.get("store_id", -1)))
        except (TypeError, ValueError):
            skipped_malformed_tx_count += 1
            continue
        if sid > 0:
            tx_by_store.setdefault(sid, []).append(tx)
    if skipped_malformed_tx_count:
        logger.warning(f"Skipped {skipped_malformed_tx_count} transaction(s) with malformed store_id")
    return tx_by_store


def _assess_eligibility(
    sid: int,
    store_tx: list[Mapping[str, Any]],
    hh_set: set[str],
    campaign_start_day: int,
    campaign_end_day: int,
) -> tuple[bool, dict[str, Any] | None, str | None]:
    """Opt-in eligibility gate: no exposure data means "not checked" (eligible),
    a computation error means "checked and errored" (fail closed)."""
    if not (hh_set and store_tx):
        return True, None, None
    try:
        details = compute_store_campaign_eligibility(
            store_id=sid,
            transactions=store_tx,
            campaign_households=hh_set,
            start_day=campaign_start_day,
            end_day=campaign_end_day,
        )
        return details.get("is_eligible", True), details, None
    except Exception as e:
        logger.warning(f"Store {sid} exposure calculation error: {e}")
        return False, None, str(e)


def _build_daily_sales(store_tx: list[Mapping[str, Any]], sid: int) -> dict[int, float]:
    daily_sales: dict[int, float] = {}
    for tx in store_tx:
        try:
            day = int(tx.get("DAY", tx.get("day", -1)))
            sales = float(tx.get("SALES_VALUE", tx.get("sales_value", 0.0)))
        except (TypeError, ValueError):
            logger.warning(f"Skipping malformed transaction record for store {sid}")
            continue
        daily_sales[day] = daily_sales.get(day, 0.0) + sales
    return daily_sales


def _window_observations(
    daily_sales: dict[int, float],
    day_range: range,
    base_start_time: datetime,
    campaign_start_day: int,
    campaign_id: str,
    timing_window: str,
    *,
    before_start: bool,
) -> list[OutcomeObservation]:
    sign = -1 if before_start else 1

    def observation_for(day: int) -> OutcomeObservation:
        offset = (campaign_start_day - day) if before_start else (day - campaign_start_day)
        return OutcomeObservation(
            observed_at=base_start_time + sign * timedelta(days=offset),
            value=daily_sales.get(day, 0.0),
            metric_name="sales",
            source="dunnhumby_tx",
            campaign_id=campaign_id,
            timing_window=timing_window,
        )

    return [observation_for(d) for d in day_range if d in daily_sales]


def _resolve_forecast(
    sid: int,
    all_stores_meta: dict[int, dict[str, Any]] | None,
    auto_forecast: bool,
    campaign_start_day: int,
) -> tuple[float | None, str, str | None]:
    """Derive the 14-day forecast reference. Returns (reference, status, error)."""
    if not auto_forecast:
        return None, "NO_DATA", None
    try:
        store_info = (
            all_stores_meta.get(sid)
            if all_stores_meta is not None
            else get_store_info(sid)
        )
        if store_info is None:
            return None, "NO_DATA", None
        forecast_ref = get_evaluation_window_forecast(
            store_id=sid,
            start_day=campaign_start_day,
            window_start_offset=47,
            window_end_offset=60,
        )
        return forecast_ref, "AVAILABLE", None
    except (requests.RequestException, TimeoutError) as e:
        return None, "ERROR", f"Network error: {e}"
    except (ForecastResponseError, ValueError, TypeError) as e:
        return None, "ERROR", f"Validation error: {e}"


def _provenance_join_state(
    sid: int,
    key: InterventionKey,
    campaign_id: str,
    timing_window: str,
    base_start_time: datetime,
    outcome: OutcomeEvaluation,
) -> str:
    rec = RecommendationRecord(
        recommendation_id=f"rec-{sid}",
        store_id=sid,
        recommendation="EXTEND_INTERVENTION",
        reason="Portfolio recovery intervention",
        generated_at=base_start_time,
        campaign_id=campaign_id,
        timing_window=timing_window,
        intervention_key=key,
    )
    appr = ApprovalRecord(
        approval_id=f"appr-{sid}",
        recommendation_id=rec.recommendation_id,
        approved=True,
        decided_at=base_start_time + timedelta(minutes=1),
    )
    intv = InterventionRecord(
        intervention_id=outcome.intervention_id,
        approval_id=appr.approval_id,
        recommendation_id=rec.recommendation_id,
        lifecycle_state="COMPLETED",
        started_at=base_start_time + timedelta(minutes=2),
        campaign_id=campaign_id,
        timing_window=timing_window,
        intervention_key=key,
    )
    checkpoints = build_weekly_checkpoints(
        intervention_id=intv.intervention_id,
        intervention_started_at=intv.started_at,
        as_of=base_start_time + timedelta(days=EVALUATION_WINDOW_DAYS),
    )
    join = build_intervention_outcome_join(rec, appr, intv, checkpoints, outcome)
    return join.evidence_state


def _evaluate_single_store(
    sid: int,
    store_tx: list[Mapping[str, Any]],
    hh_set: set[str],
    all_stores_meta: dict[int, dict[str, Any]] | None,
    *,
    auto_forecast: bool,
    campaign_start_day: int,
    campaign_end_day: int,
    intervention_type: str,
    strategy_version: str,
    campaign_id: str,
    timing_window: str,
) -> StoreEvaluationResult:
    base_start_time = datetime(2018, 8, 10, 0, 0, tzinfo=timezone.utc)
    is_eligible, eligibility_details, eligibility_error = _assess_eligibility(
        sid, store_tx, hh_set, campaign_start_day, campaign_end_day,
    )
    daily_sales = _build_daily_sales(store_tx, sid)

    # Baseline window: [campaign_start_day - 56, campaign_start_day)
    baseline_start_day = campaign_start_day - BASELINE_DAYS
    baseline_obs = _window_observations(
        daily_sales, range(baseline_start_day, campaign_start_day),
        base_start_time, campaign_start_day, campaign_id, timing_window,
        before_start=True,
    )
    # Recent 14-day window: [campaign_start_day + 46, campaign_start_day + 60]
    recent_start_day = campaign_start_day + 46
    recent_end_day = campaign_start_day + EVALUATION_WINDOW_DAYS
    recent_obs = _window_observations(
        daily_sales, range(recent_start_day, recent_end_day + 1),
        base_start_time, campaign_start_day, campaign_id, timing_window,
        before_start=False,
    )

    forecast_ref, forecast_status, error_msg = _resolve_forecast(
        sid, all_stores_meta, auto_forecast, campaign_start_day,
    )

    key = InterventionKey(
        store_id=sid,
        intervention_type=intervention_type,
        target_segment="loyal",
        campaign_variant=None,
        strategy_version=strategy_version,
    )
    calc = evaluate_outcome(
        intervention_id=f"int-{sid}-{uuid.uuid4().hex[:8]}",
        intervention_key=key,
        intervention_started_at=base_start_time,
        observations=baseline_obs + recent_obs,
        as_of=base_start_time + timedelta(days=EVALUATION_WINDOW_DAYS),
        forecast_reference_value=forecast_ref,
        forecast_status=forecast_status,
        campaign_id=campaign_id,
        timing_window=timing_window,
    )
    join_state = _provenance_join_state(
        sid, key, campaign_id, timing_window, base_start_time, calc.outcome,
    )

    return StoreEvaluationResult(
        store_id=sid,
        is_eligible=is_eligible,
        evidence_state=calc.evidence_state,
        forecast_status=forecast_status,
        baseline_daily_mean=calc.outcome.baseline_value,
        recent_daily_mean=calc.outcome.recent_observation_value,
        forecast_reference_value=forecast_ref,
        longitudinal_uplift_pct=calc.outcome.longitudinal_uplift_pct,
        counterfactual_uplift_pct=calc.outcome.counterfactual_uplift_pct,
        recovery_pct_of_target=calc.outcome.recovery_pct_of_target,
        join_state=join_state,
        eligibility_details=eligibility_details,
        eligibility_error=eligibility_error,
        outcome=calc.outcome,
        error=error_msg,
    )


def evaluate_store_portfolio(
    store_ids: Sequence[int],
    *,
    transactions: Sequence[Mapping[str, Any]] = (),
    campaign_households: set[str] | Sequence[str] = (),
    campaign_start_day: int = 587,
    campaign_end_day: int = 642,
    intervention_type: str = "recovery",
    strategy_version: str = "v1",
    auto_forecast: bool = True,
    campaign_id: str = "18",
    timing_window: str = "12 PM - 6 PM",
) -> PortfolioEvaluationReport:
    """Evaluate a portfolio of retail stores against real transaction observations and forecast references."""
    hh_set = set(str(hh) for hh in campaign_households)
    tx_by_store = _index_transactions_by_store(transactions)

    all_stores_meta: dict[int, dict[str, Any]] | None = None
    if auto_forecast:
        try:
            all_stores_meta = get_all_stores_info()
        except Exception as e:
            logger.warning(f"Could not bulk fetch store metadata from forecast API: {e}")
            all_stores_meta = None

    results = [
        _evaluate_single_store(
            sid,
            tx_by_store.get(sid, []),
            hh_set,
            all_stores_meta,
            auto_forecast=auto_forecast,
            campaign_start_day=campaign_start_day,
            campaign_end_day=campaign_end_day,
            intervention_type=intervention_type,
            strategy_version=strategy_version,
            campaign_id=campaign_id,
            timing_window=timing_window,
        )
        for sid in store_ids
    ]

    # Portfolio Aggregations
    return _build_portfolio_report(results, store_ids)


def _pooled_sums(results: list[StoreEvaluationResult]) -> tuple[float, float]:
    """Pooled 14-day recent sales and counterfactual forecast totals."""
    pooled_actual = sum(
        (r.recent_daily_mean * RECENT_OBSERVATION_DAYS)
        for r in results
        if r.recent_daily_mean is not None
    )
    pooled_fcst = sum(
        (r.forecast_reference_value * RECENT_OBSERVATION_DAYS)
        for r in results
        if r.forecast_reference_value is not None
    )
    return pooled_actual, pooled_fcst


def _pooled_uplift_pct(pooled_actual: float, pooled_fcst: float) -> float | None:
    return ((pooled_actual - pooled_fcst) / pooled_fcst * 100) if pooled_fcst > 0 else None


def _uplift_averages(results: list[StoreEvaluationResult]) -> tuple[list[float], list[float]]:
    valid_long = [r.longitudinal_uplift_pct for r in results if r.longitudinal_uplift_pct is not None]
    valid_count = [r.counterfactual_uplift_pct for r in results if r.counterfactual_uplift_pct is not None]
    return valid_long, valid_count


def _build_portfolio_report(
    results: list[StoreEvaluationResult], store_ids: Sequence[int]
) -> PortfolioEvaluationReport:
    valid_long, valid_count = _uplift_averages(results)
    pooled_actual, pooled_fcst = _pooled_sums(results)
    pooled_uplift = _pooled_uplift_pct(pooled_actual, pooled_fcst)

    methodology = {
        "portfolio_scope": f"Evaluated {len(store_ids)} stores across 56-day baseline and 14-day recent observation windows",
        "longitudinal_metric": "Store-level daily mean comparison: (recent_14d_mean - baseline_56d_mean) / baseline_56d_mean * 100",
        "counterfactual_metric": "Store-level predictive comparison: (recent_14d_mean - forecast_reference_value) / forecast_reference_value * 100",
        "pooled_uplift": "Cumulative 14-day window sales vs counterfactual forecast across all evaluated stores",
        "target_benchmark": f"{TARGET_UPLIFT_PCT}% pooled Campaign 18 pilot benchmark",
    }

    return PortfolioEvaluationReport(
        total_stores=len(store_ids),
        eligible_stores_count=sum(1 for r in results if r.is_eligible),
        sufficient_evidence_count=sum(1 for r in results if r.evidence_state == "SUFFICIENT"),
        available_forecast_count=sum(1 for r in results if r.forecast_status == "AVAILABLE"),
        error_count=sum(1 for r in results if r.forecast_status == "ERROR"),
        mean_longitudinal_uplift_pct=fmean(valid_long) if valid_long else None,
        mean_counterfactual_uplift_pct=fmean(valid_count) if valid_count else None,
        median_counterfactual_uplift_pct=median(valid_count) if valid_count else None,
        pooled_actual_recent_sales=round(pooled_actual, 2),
        pooled_counterfactual_forecast_sales=round(pooled_fcst, 2),
        pooled_counterfactual_uplift_pct=round(pooled_uplift, 2) if pooled_uplift is not None else None,
        target_benchmark_pct=TARGET_UPLIFT_PCT,
        store_results=tuple(results),
        methodology=methodology,
    )
