"""Deterministic Phase 2 outcome evaluator.

MVP behavior:
- 56-day baseline window immediately before intervention start;
- 14-day recent observation window at the end of the 60-day recovery window;
- outcome success is only possible when both windows have sufficient, non-contradictory data;
- forecast/reference data is contextual only and never establishes success.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import fmean
from typing import Iterable, Sequence
import uuid

from .contracts import (
    APPROVED,
    CheckpointRecord,
    CHECKPOINT_STATUSES,
    EVIDENCE_STATES,
    CONTRADICTORY,
    DUE,
    EVALUATED,
    FAILED,
    INSUFFICIENT,
    INVALID,
    MISSED,
    NOT_DUE,
    OBSERVED,
    OUTCOME_PENDING,
    PARTIAL,
    SUFFICIENT,
    InterventionKey,
    InterventionRecord,
    RecommendationRecord,
    ApprovalRecord,
    OutcomeEvaluation,
    OutcomeObservation,
    JoinedInterventionTimeline,
)

BASELINE_DAYS = 56
RECENT_OBSERVATION_DAYS = 14
EVALUATION_WINDOW_DAYS = 60
TARGET_UPLIFT_PCT = 30.1


@dataclass(frozen=True)
class OutcomeCalculation:
    outcome: OutcomeEvaluation
    evidence_state: str


@dataclass(frozen=True)
class JoinValidationResult:
    evidence_state: str
    joined: JoinedInterventionTimeline | None
    reason: str | None = None
    conflicts: tuple[str, ...] = ()


def evaluate_outcome(
    *,
    intervention_id: str,
    intervention_key: InterventionKey,
    intervention_started_at: datetime,
    observations: Sequence[OutcomeObservation],
    as_of: datetime | None = None,
    outcome_id: str | None = None,
    forecast_reference_value: float | None = None,
    forecast_status: str | None = None,
    campaign_id: str | None = None,
    timing_window: str | None = None,
) -> OutcomeCalculation:
    """Evaluate the intervention outcome with the locked 56/14-day method.

    The recent window is the final 14 days of the 60-day evaluation period.
    The evaluator uses arithmetic means per window and classifies incomplete
    evidence deterministically rather than inventing success.
    """
    if outcome_id is None:
        outcome_id = f"outcome-{uuid.uuid4().hex}"
    _validate_aware_datetime(intervention_started_at, "intervention_started_at")
    current_time = _utc(as_of or datetime.now(timezone.utc))
    started_at = _utc(intervention_started_at)
    evaluation_due_at = started_at + timedelta(days=EVALUATION_WINDOW_DAYS)
    baseline_window_start = started_at - timedelta(days=BASELINE_DAYS)
    baseline_window_end = started_at
    recent_window_start = evaluation_due_at - timedelta(days=RECENT_OBSERVATION_DAYS)
    recent_window_end = evaluation_due_at

    buckets = _bucket_observations(observations, baseline_window_start, baseline_window_end, recent_window_start, recent_window_end)
    observed_at = _latest_observation_time(buckets)

    evidence_state = _classify_evidence(current_time, evaluation_due_at, buckets)
    baseline_value = None
    recent_value = None
    actual_uplift_pct = None
    recovery_pct_of_target = None

    try:
        baseline_value, recent_value = _summarize_windows(buckets)
    except ValueError:
        evidence_state = CONTRADICTORY
    else:
        if evidence_state == SUFFICIENT:
            if baseline_value is None or baseline_value <= 0:
                evidence_state = INVALID
            else:
                actual_uplift_pct = (recent_value - baseline_value) / baseline_value * 100
                recovery_pct_of_target = actual_uplift_pct / TARGET_UPLIFT_PCT * 100

    outcome = OutcomeEvaluation(
        outcome_id=outcome_id,
        intervention_id=intervention_id,
        intervention_key=intervention_key,
        evidence_state=evidence_state,
        evaluation_due_at=evaluation_due_at,
        observed_at=observed_at,
        baseline_window_start=baseline_window_start,
        baseline_window_end=baseline_window_end,
        recent_window_start=recent_window_start,
        recent_window_end=recent_window_end,
        baseline_value=baseline_value,
        recent_observation_value=recent_value,
        actual_uplift_pct=actual_uplift_pct,
        recovery_pct_of_target=recovery_pct_of_target,
        forecast_reference_value=forecast_reference_value,
        forecast_status=forecast_status,
        campaign_id=campaign_id,
        timing_window=timing_window,
    )
    return OutcomeCalculation(outcome=outcome, evidence_state=evidence_state)


def build_weekly_checkpoints(
    *,
    intervention_id: str,
    intervention_started_at: datetime,
    observed_checkpoints: Sequence[CheckpointRecord] = (),
    as_of: datetime | None = None,
    weeks: int = 8,
) -> tuple[CheckpointRecord, ...]:
    _validate_aware_datetime(intervention_started_at, "intervention_started_at")
    current_time = _utc(as_of or datetime.now(timezone.utc))
    start = _utc(intervention_started_at)
    observed_by_due: dict[datetime, list[CheckpointRecord]] = {}
    for record in observed_checkpoints:
        due_at = _utc(record.due_at)
        observed_by_due.setdefault(due_at, []).append(record)

    checkpoints: list[CheckpointRecord] = []
    for week in range(1, weeks + 1):
        due_at = start + timedelta(days=7 * week)
        provided = observed_by_due.get(due_at, [])
        if len(provided) > 1:
            checkpoints.append(
                CheckpointRecord(
                    checkpoint_id=provided[0].checkpoint_id,
                    intervention_id=intervention_id,
                    due_at=due_at,
                    observed_at=None,
                    status=INVALID,
                    metric_name=provided[0].metric_name,
                    metric_value=provided[0].metric_value,
                    source=provided[0].source,
                    campaign_id=provided[0].campaign_id,
                    timing_window=provided[0].timing_window,
                    intervention_key=provided[0].intervention_key,
                )
            )
            continue
        if provided:
            record = provided[0]
            status = _checkpoint_status(record, current_time, due_at)
            checkpoints.append(
                CheckpointRecord(
                    checkpoint_id=record.checkpoint_id,
                    intervention_id=intervention_id,
                    due_at=due_at,
                    observed_at=record.observed_at,
                    status=status,
                    metric_name=record.metric_name,
                    metric_value=record.metric_value,
                    source=record.source,
                    campaign_id=record.campaign_id,
                    timing_window=record.timing_window,
                    intervention_key=record.intervention_key,
                )
            )
            continue
        status = DUE if current_time < due_at else MISSED
        checkpoints.append(
            CheckpointRecord(
                checkpoint_id=f"checkpoint-{week}-{uuid.uuid4().hex}",
                intervention_id=intervention_id,
                due_at=due_at,
                observed_at=None,
                status=status,
            )
        )
    return tuple(checkpoints)


def build_intervention_outcome_join(
    recommendation: RecommendationRecord,
    approval: ApprovalRecord,
    intervention: InterventionRecord,
    checkpoints: Sequence[CheckpointRecord],
    outcome: OutcomeEvaluation,
) -> JoinValidationResult:
    if approval.recommendation_id != recommendation.recommendation_id:
        return JoinValidationResult("INVALID", None, "approval does not reference the recommendation")
    if not approval.approved:
        return JoinValidationResult("INVALID", None, "approval is rejected or unapproved")
    if intervention.approval_id != approval.approval_id:
        return JoinValidationResult("INVALID", None, "intervention does not reference the approval")
    if intervention.recommendation_id != recommendation.recommendation_id:
        return JoinValidationResult("INVALID", None, "intervention does not reference the recommendation")
    if outcome.intervention_id != intervention.intervention_id:
        return JoinValidationResult("INVALID", None, "outcome does not reference the intervention")
    if intervention.lifecycle_state not in {APPROVED, "ACTIVE", "PAUSED", "COMPLETED", FAILED, OUTCOME_PENDING, EVALUATED}:
        return JoinValidationResult("INVALID", None, "intervention lifecycle state is not outcome-evaluable")
    if intervention.started_at is None:
        return JoinValidationResult("INVALID", None, "outcome-evaluable intervention has no start timestamp")
    if outcome.evidence_state not in EVIDENCE_STATES:
        return JoinValidationResult("INVALID", None, "unsupported outcome evidence state")
    if outcome.evidence_state != SUFFICIENT:
        return JoinValidationResult(outcome.evidence_state, None, "outcome evidence is not sufficient")

    conflicts = _join_conflicts(recommendation, intervention, outcome)
    if conflicts:
        evidence_state = "CONTRADICTORY" if any(field in {"campaign_id", "timing_window"} for field in conflicts) else "INVALID"
        return JoinValidationResult(evidence_state, None, "provenance conflict", conflicts=conflicts)

    temporal_error = _temporal_join_error(recommendation, approval, intervention, checkpoints, outcome)
    if temporal_error is not None:
        return JoinValidationResult("INVALID", None, temporal_error)

    for checkpoint in checkpoints:
        if checkpoint.intervention_id != intervention.intervention_id:
            return JoinValidationResult("INVALID", None, "checkpoint does not reference the intervention")
        if checkpoint.status not in CHECKPOINT_STATUSES or checkpoint.status == INVALID:
            return JoinValidationResult("INVALID", None, "checkpoint evidence is invalid")
        checkpoint_conflicts = _checkpoint_conflicts(checkpoint, recommendation, intervention, outcome)
        if checkpoint_conflicts:
            checkpoint_state = "CONTRADICTORY" if any(field in {"campaign_id", "timing_window"} for field in checkpoint_conflicts) else "INVALID"
            return JoinValidationResult(checkpoint_state, None, "checkpoint provenance conflict", conflicts=checkpoint_conflicts)

    return JoinValidationResult(
        "SUFFICIENT",
        JoinedInterventionTimeline(
            recommendation=recommendation,
            approval=approval,
            intervention=intervention,
            checkpoints=tuple(checkpoints),
            outcome=outcome,
            campaign_id=intervention.campaign_id if intervention.campaign_id is not None else recommendation.campaign_id,
            timing_window=intervention.timing_window if intervention.timing_window is not None else recommendation.timing_window,
        ),
    )


def _join_conflicts(
    recommendation: RecommendationRecord,
    intervention: InterventionRecord,
    outcome: OutcomeEvaluation,
) -> tuple[str, ...]:
    conflicts: list[str] = []
    if recommendation.store_id != intervention.intervention_key.store_id:
        conflicts.append("store_id")
    if intervention.intervention_key != recommendation.intervention_key:
        conflicts.append("canonical InterventionKey")
    if intervention.intervention_key != outcome.intervention_key:
        conflicts.append("outcome canonical InterventionKey")
    if _conflicting_shared_value(recommendation.campaign_id, intervention.campaign_id, outcome.campaign_id):
        conflicts.append("campaign_id")
    if _conflicting_shared_value(recommendation.timing_window, intervention.timing_window, outcome.timing_window):
        conflicts.append("timing_window")
    return tuple(conflicts)


def _conflicting_shared_value(*values: str | None) -> bool:
    provided = [value for value in values if value is not None]
    if len(provided) <= 1:
        return False
    return any(value != provided[0] for value in provided[1:])



def _checkpoint_conflicts(
    checkpoint: CheckpointRecord,
    recommendation: RecommendationRecord,
    intervention: InterventionRecord,
    outcome: OutcomeEvaluation,
) -> tuple[str, ...]:
    conflicts: list[str] = []
    if checkpoint.intervention_key is not None and checkpoint.intervention_key != intervention.intervention_key:
        conflicts.append("checkpoint canonical InterventionKey")
    if _checkpoint_field_conflict(
        checkpoint.campaign_id, recommendation.campaign_id, intervention.campaign_id, outcome.campaign_id
    ):
        conflicts.append("campaign_id")
    if _checkpoint_field_conflict(
        checkpoint.timing_window, recommendation.timing_window, intervention.timing_window, outcome.timing_window
    ):
        conflicts.append("timing_window")
    return tuple(conflicts)


def _checkpoint_field_conflict(checkpoint_value: str | None, *other_values: str | None) -> bool:
    if checkpoint_value is None:
        return False
    provided = [value for value in other_values if value is not None]
    return not provided or any(value != checkpoint_value for value in provided)


def _temporal_join_error(
    recommendation: RecommendationRecord,
    approval: ApprovalRecord,
    intervention: InterventionRecord,
    checkpoints: Sequence[CheckpointRecord],
    outcome: OutcomeEvaluation,
) -> str | None:
    generated_at = _utc(recommendation.generated_at)
    decided_at = _utc(approval.decided_at)
    started_at = _utc(intervention.started_at)
    if generated_at > decided_at:
        return "approval precedes recommendation"
    if started_at < decided_at:
        return "intervention starts before approval"
    if _utc(outcome.baseline_window_end) > started_at:
        return "outcome baseline extends past intervention start"
    if _utc(outcome.evaluation_due_at) < started_at:
        return "outcome evaluation precedes intervention start"
    if _utc(outcome.recent_window_start) < _utc(outcome.baseline_window_end):
        return "outcome windows overlap or are reversed"
    for checkpoint in checkpoints:
        due_at = _utc(checkpoint.due_at)
        if due_at < started_at:
            return "checkpoint is due before intervention start"
        if checkpoint.observed_at is not None and _utc(checkpoint.observed_at) < due_at:
            return "checkpoint observed before due timestamp"
    return None

def _bucket_observations(
    observations: Sequence[OutcomeObservation],
    baseline_window_start: datetime,
    baseline_window_end: datetime,
    recent_window_start: datetime,
    recent_window_end: datetime,
) -> dict[str, list[OutcomeObservation]]:
    buckets = {"baseline": [], "recent": []}
    for observation in observations:
        _validate_aware_datetime(observation.observed_at, "observed_at")
        timestamp = _utc(observation.observed_at)
        if baseline_window_start <= timestamp < baseline_window_end:
            buckets["baseline"].append(observation)
        elif recent_window_start <= timestamp <= recent_window_end:
            buckets["recent"].append(observation)
    return buckets


def _summarize_windows(buckets: dict[str, list[OutcomeObservation]]) -> tuple[float | None, float | None]:
    return _window_average(buckets["baseline"]), _window_average(buckets["recent"])


def _window_average(observations: Sequence[OutcomeObservation]) -> float | None:
    if not observations:
        return None
    values_by_timestamp: dict[datetime, float] = {}
    for observation in observations:
        timestamp = _utc(observation.observed_at)
        value = float(observation.value)
        existing = values_by_timestamp.get(timestamp)
        if existing is not None and existing != value:
            raise ValueError("contradictory observations at the same timestamp")
        values_by_timestamp[timestamp] = value
    return fmean(values_by_timestamp.values())


def _classify_evidence(current_time: datetime, due_at: datetime, buckets: dict[str, list[OutcomeObservation]]) -> str:
    if current_time < due_at:
        return NOT_DUE
    if not buckets["baseline"] and not buckets["recent"]:
        return INSUFFICIENT
    if not buckets["baseline"] or not buckets["recent"]:
        return PARTIAL
    return SUFFICIENT


def _latest_observation_time(buckets: dict[str, list[OutcomeObservation]]) -> datetime | None:
    all_observations = buckets["baseline"] + buckets["recent"]
    if not all_observations:
        return None
    return max(_utc(observation.observed_at) for observation in all_observations)


def _checkpoint_status(record: CheckpointRecord, current_time: datetime, due_at: datetime) -> str:
    if record.observed_at is None:
        return DUE if current_time < due_at else MISSED
    try:
        _validate_aware_datetime(record.observed_at, "observed_at")
    except (TypeError, ValueError):
        return INVALID
    return OBSERVED


def _validate_aware_datetime(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)
