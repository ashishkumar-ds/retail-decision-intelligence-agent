"""Core Phase 2 contracts.

MVP policy choices documented here:
- canonical string values are preserved exactly; validation only rejects blank
  or whitespace-only strings for canonical identity fields;
- timestamps must be timezone-aware and are normalized to UTC for comparison;
- append-only event logs preserve file order as the tie-breaker during replay.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

RECOMMENDED = "RECOMMENDED"
APPROVED = "APPROVED"
ACTIVE = "ACTIVE"
PAUSED = "PAUSED"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
REJECTED = "REJECTED"
EXPIRED = "EXPIRED"
CANCELLED = "CANCELLED"
OUTCOME_PENDING = "OUTCOME_PENDING"
EVALUATED = "EVALUATED"

DUE = "DUE"
OBSERVED = "OBSERVED"
MISSED = "MISSED"
INVALID = "INVALID"
NOT_DUE = "NOT_DUE"
PARTIAL = "PARTIAL"
SUFFICIENT = "SUFFICIENT"
INSUFFICIENT = "INSUFFICIENT"
CONTRADICTORY = "CONTRADICTORY"

EVIDENCE_STATES = frozenset({NOT_DUE, PARTIAL, SUFFICIENT, INSUFFICIENT, INVALID, CONTRADICTORY})
CHECKPOINT_STATUSES = frozenset({DUE, OBSERVED, MISSED, INVALID})

ACTIVE_INTERVENTION_STATES = frozenset({APPROVED, ACTIVE, PAUSED})
TERMINAL_INTERVENTION_STATES = frozenset({REJECTED, EXPIRED, CANCELLED, COMPLETED, FAILED, EVALUATED})


class InterventionLifecycleState:
    RECOMMENDED = RECOMMENDED
    APPROVED = APPROVED
    ACTIVE = ACTIVE
    PAUSED = PAUSED
    COMPLETED = COMPLETED
    FAILED = FAILED
    REJECTED = REJECTED
    EXPIRED = EXPIRED
    CANCELLED = CANCELLED
    OUTCOME_PENDING = OUTCOME_PENDING
    EVALUATED = EVALUATED


class CheckpointStatus:
    DUE = DUE
    OBSERVED = OBSERVED
    MISSED = MISSED
    INVALID = INVALID


@dataclass(frozen=True)
class InterventionKey:
    store_id: int
    intervention_type: str
    target_segment: str | None
    campaign_variant: str | None
    strategy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.store_id, int) or isinstance(self.store_id, bool):
            raise TypeError("store_id must be an int")
        _validate_nonblank_text(self.intervention_type, "intervention_type")
        _validate_optional_text(self.target_segment, "target_segment")
        _validate_optional_text(self.campaign_variant, "campaign_variant")
        _validate_nonblank_text(self.strategy_version, "strategy_version")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "intervention_type": self.intervention_type,
            "target_segment": self.target_segment,
            "campaign_variant": self.campaign_variant,
            "strategy_version": self.strategy_version,
        }

    def canonical_json(self) -> str:
        return json.dumps(self.canonical_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "InterventionKey":
        return cls(
            store_id=mapping["store_id"],
            intervention_type=mapping["intervention_type"],
            target_segment=mapping.get("target_segment"),
            campaign_variant=mapping.get("campaign_variant"),
            strategy_version=mapping["strategy_version"],
        )


@dataclass(frozen=True)
class InterventionEvent:
    event_id: str
    intervention_id: str
    event_type: str
    occurred_at: datetime
    key: InterventionKey
    campaign_id: str | None = None
    timing_window: str | None = None
    recommendation_id: str | None = None
    approval_id: str | None = None
    checkpoint_id: str | None = None
    outcome_id: str | None = None
    actor: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_nonblank_text(self.event_id, "event_id")
        _validate_nonblank_text(self.intervention_id, "intervention_id")
        _validate_nonblank_text(self.event_type, "event_type")
        _validate_aware_datetime(self.occurred_at, "occurred_at")
        _validate_optional_text(self.campaign_id, "campaign_id")
        _validate_optional_text(self.timing_window, "timing_window")
        _validate_optional_text(self.recommendation_id, "recommendation_id")
        _validate_optional_text(self.approval_id, "approval_id")
        _validate_optional_text(self.checkpoint_id, "checkpoint_id")
        _validate_optional_text(self.outcome_id, "outcome_id")
        _validate_optional_text(self.actor, "actor")
        if not isinstance(self.payload, dict):
            raise TypeError("payload must be a dict")

    def to_record(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "intervention_id": self.intervention_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.astimezone(timezone.utc).isoformat(),
            "key": self.key.canonical_dict(),
            "campaign_id": self.campaign_id,
            "timing_window": self.timing_window,
            "recommendation_id": self.recommendation_id,
            "approval_id": self.approval_id,
            "checkpoint_id": self.checkpoint_id,
            "outcome_id": self.outcome_id,
            "actor": self.actor,
            "payload": self.payload,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "InterventionEvent":
        return cls(
            event_id=record["event_id"],
            intervention_id=record["intervention_id"],
            event_type=record["event_type"],
            occurred_at=_parse_aware_datetime(record["occurred_at"]),
            key=InterventionKey.from_mapping(record["key"]),
            campaign_id=record.get("campaign_id"),
            timing_window=record.get("timing_window"),
            recommendation_id=record.get("recommendation_id"),
            approval_id=record.get("approval_id"),
            checkpoint_id=record.get("checkpoint_id"),
            outcome_id=record.get("outcome_id"),
            actor=record.get("actor"),
            payload=dict(record.get("payload", {})),
        )


@dataclass(frozen=True)
class RecommendationRecord:
    recommendation_id: str
    store_id: int
    intervention_key: InterventionKey
    recommendation: str
    generated_at: datetime
    reason: str
    campaign_id: str | None = None
    timing_window: str | None = None

    def __post_init__(self) -> None:
        _validate_nonblank_text(self.recommendation_id, "recommendation_id")
        if not isinstance(self.store_id, int) or isinstance(self.store_id, bool):
            raise TypeError("store_id must be an int")
        _validate_nonblank_text(self.recommendation, "recommendation")
        _validate_aware_datetime(self.generated_at, "generated_at")
        _validate_nonblank_text(self.reason, "reason")
        _validate_optional_text(self.campaign_id, "campaign_id")
        _validate_optional_text(self.timing_window, "timing_window")


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    recommendation_id: str
    approved: bool
    decided_at: datetime
    approver: str | None = None

    def __post_init__(self) -> None:
        _validate_nonblank_text(self.approval_id, "approval_id")
        _validate_nonblank_text(self.recommendation_id, "recommendation_id")
        if not isinstance(self.approved, bool):
            raise TypeError("approved must be a bool")
        _validate_aware_datetime(self.decided_at, "decided_at")
        _validate_optional_text(self.approver, "approver")


@dataclass(frozen=True)
class InterventionRecord:
    intervention_id: str
    approval_id: str
    recommendation_id: str
    intervention_key: InterventionKey
    lifecycle_state: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    campaign_id: str | None = None
    timing_window: str | None = None

    def __post_init__(self) -> None:
        _validate_nonblank_text(self.intervention_id, "intervention_id")
        _validate_nonblank_text(self.approval_id, "approval_id")
        _validate_nonblank_text(self.recommendation_id, "recommendation_id")
        _validate_nonblank_text(self.lifecycle_state, "lifecycle_state")
        _validate_optional_aware_datetime(self.started_at, "started_at")
        _validate_optional_aware_datetime(self.ended_at, "ended_at")
        _validate_optional_text(self.campaign_id, "campaign_id")
        _validate_optional_text(self.timing_window, "timing_window")


@dataclass(frozen=True)
class CheckpointRecord:
    checkpoint_id: str
    intervention_id: str
    due_at: datetime
    observed_at: datetime | None = None
    status: str = DUE
    metric_name: str = "sales"
    metric_value: float | None = None
    source: str = "project2"
    campaign_id: str | None = None
    timing_window: str | None = None
    intervention_key: InterventionKey | None = None

    def __post_init__(self) -> None:
        _validate_nonblank_text(self.checkpoint_id, "checkpoint_id")
        _validate_nonblank_text(self.intervention_id, "intervention_id")
        _validate_aware_datetime(self.due_at, "due_at")
        _validate_optional_aware_datetime(self.observed_at, "observed_at")
        _validate_nonblank_text(self.status, "status")
        _validate_nonblank_text(self.metric_name, "metric_name")
        _validate_nonblank_text(self.source, "source")
        if self.metric_value is not None and isinstance(self.metric_value, bool):
            raise TypeError("metric_value must be numeric or None")
        _validate_optional_text(self.campaign_id, "campaign_id")
        _validate_optional_text(self.timing_window, "timing_window")
        if self.intervention_key is not None and not isinstance(self.intervention_key, InterventionKey):
            raise TypeError("intervention_key must be an InterventionKey or None")


@dataclass(frozen=True)
class OutcomeObservation:
    observed_at: datetime
    value: float
    metric_name: str = "sales"
    source: str = "project2"
    campaign_id: str | None = None
    timing_window: str | None = None

    def __post_init__(self) -> None:
        _validate_aware_datetime(self.observed_at, "observed_at")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise TypeError("value must be numeric")
        _validate_nonblank_text(self.metric_name, "metric_name")
        _validate_nonblank_text(self.source, "source")
        _validate_optional_text(self.campaign_id, "campaign_id")
        _validate_optional_text(self.timing_window, "timing_window")


@dataclass(frozen=True)
class OutcomeEvaluation:
    outcome_id: str
    intervention_id: str
    intervention_key: InterventionKey
    evidence_state: str
    evaluation_due_at: datetime
    observed_at: datetime | None
    baseline_window_start: datetime
    baseline_window_end: datetime
    recent_window_start: datetime
    recent_window_end: datetime
    baseline_value: float | None
    recent_observation_value: float | None
    actual_uplift_pct: float | None
    recovery_pct_of_target: float | None
    target_assessment: str | None = None  # MEETS_TARGET / REVIEW_ZONE / NEGATIVE (None if not SUFFICIENT)
    forecast_reference_value: float | None = None
    forecast_status: str | None = None
    campaign_id: str | None = None
    timing_window: str | None = None
    longitudinal_uplift_pct: float | None = None
    counterfactual_uplift_pct: float | None = None
    methodology: dict[str, str] = field(default_factory=dict)

    _BOOL_FREE_NUMERIC_FIELDS = (
        "baseline_value", "recent_observation_value", "actual_uplift_pct",
        "recovery_pct_of_target", "forecast_reference_value",
    )
    _STRICT_NUMERIC_FIELDS = ("longitudinal_uplift_pct", "counterfactual_uplift_pct")
    _AWARE_DATETIME_FIELDS = (
        "evaluation_due_at", "baseline_window_start", "baseline_window_end",
        "recent_window_start", "recent_window_end",
    )

    def __post_init__(self) -> None:
        for name in ("outcome_id", "intervention_id", "evidence_state"):
            _validate_nonblank_text(getattr(self, name), name)
        if self.evidence_state not in EVIDENCE_STATES:
            raise ValueError(f"unsupported evidence_state {self.evidence_state}")
        for name in self._AWARE_DATETIME_FIELDS:
            _validate_aware_datetime(getattr(self, name), name)
        _validate_optional_aware_datetime(self.observed_at, "observed_at")
        for name in self._BOOL_FREE_NUMERIC_FIELDS:
            value = getattr(self, name)
            if value is not None and isinstance(value, bool):
                raise TypeError(f"{name} must be numeric or None")
        if self.forecast_status is not None:
            _validate_nonblank_text(self.forecast_status, "forecast_status")
        _validate_optional_text(self.campaign_id, "campaign_id")
        _validate_optional_text(self.timing_window, "timing_window")
        for name in self._STRICT_NUMERIC_FIELDS:
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise TypeError(f"{name} must be numeric or None")
        if not isinstance(self.methodology, dict):
            raise TypeError("methodology must be a dict")


@dataclass(frozen=True)
class InvalidEvent:
    event: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class InterventionSnapshot:
    intervention_id: str
    key: InterventionKey
    lifecycle_state: str
    recommendation_id: str | None
    approval_id: str | None
    campaign_id: str | None
    timing_window: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    checkpoint_ids: tuple[str, ...] = ()
    outcome_id: str | None = None
    execution_evidence: dict[str, Any] | None = None

    @property
    def is_active(self) -> bool:
        return self.lifecycle_state in ACTIVE_INTERVENTION_STATES and self.ended_at is None


@dataclass(frozen=True)
class ReconstructionResult:
    snapshots: dict[str, InterventionSnapshot]
    invalid_events: tuple[InvalidEvent, ...]


@dataclass(frozen=True)
class JoinedInterventionTimeline:
    recommendation: RecommendationRecord
    approval: ApprovalRecord
    intervention: InterventionRecord
    checkpoints: tuple[CheckpointRecord, ...]
    outcome: OutcomeEvaluation
    campaign_id: str | None
    timing_window: str | None


def _validate_nonblank_text(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _validate_optional_text(value: str | None, field_name: str) -> None:
    if value is None:
        return
    _validate_nonblank_text(value, field_name)


def _validate_aware_datetime(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _validate_optional_aware_datetime(value: datetime | None, field_name: str) -> None:
    if value is None:
        return
    _validate_aware_datetime(value, field_name)


def _parse_aware_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp fields must be strings")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _validate_aware_datetime(parsed, "timestamp")
    return parsed.astimezone(timezone.utc)
