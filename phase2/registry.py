"""Append-only intervention registry and deterministic replay helpers."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools.campaign_tool import (
    canonical_timing_window,
    first_run_for_store,
    normalize_campaign_id,
)

from .contracts import (
    ACTIVE,
    APPROVED,
    CANCELLED,
    COMPLETED,
    EVALUATED,
    EXPIRED,
    FAILED,
    OUTCOME_PENDING,
    PAUSED,
    RECOMMENDED,
    REJECTED,
    InterventionEvent,
    InterventionKey,
    InterventionSnapshot,
    InvalidEvent,
    ReconstructionResult,
)

DEFAULT_REGISTRY_PATH = Path("logs/phase2/interventions.jsonl")
_VALID_EVENT_TYPES = {
    "define",
    "approve",
    "start",
    "pause",
    "resume",
    "complete",
    "fail",
    "reject",
    "expire",
    "cancel",
    "outcome_pending",
    "evaluate",
    "checkpoint",
}


class InterventionRegistry:
    """Small append-only JSONL registry for governed intervention events."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("PHASE_2_INTERVENTION_LOG_PATH", str(DEFAULT_REGISTRY_PATH)))

    def append_event(self, event: InterventionEvent) -> None:
        if any(existing.event_id == event.event_id for existing in self.read_events()):
            raise ValueError(f"duplicate event_id {event.event_id}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_record(), default=str, ensure_ascii=False) + "\n")

    def read_events(self) -> list[InterventionEvent]:
        events, _ = self._read_events_with_diagnostics()
        return events

    def _read_events_with_diagnostics(self) -> tuple[list[InterventionEvent], tuple[InvalidEvent, ...]]:
        if not self.path.exists():
            return [], ()
        events: list[InterventionEvent] = []
        diagnostics: list[InvalidEvent] = []
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    diagnostics.append(InvalidEvent(
                        event={"line_number": line_number, "raw": line.rstrip("\n")},
                        reason="malformed JSONL record",
                    ))
                    continue
                if not isinstance(record, dict):
                    diagnostics.append(InvalidEvent(
                        event={"line_number": line_number, "raw": record},
                        reason="registry record must be an object",
                    ))
                    continue
                try:
                    events.append(InterventionEvent.from_record(record))
                except (KeyError, TypeError, ValueError):
                    diagnostics.append(InvalidEvent(
                        event={"line_number": line_number, "record": record},
                        reason="invalid intervention event record",
                    ))
        return events, tuple(diagnostics)

    def reconstruct(self) -> ReconstructionResult:
        events, diagnostics = self._read_events_with_diagnostics()
        return reconstruct_interventions(events, diagnostics=diagnostics)

    def record_definition(
        self,
        *,
        intervention_id: str,
        recommendation_id: str,
        key: InterventionKey,
        occurred_at: datetime,
        campaign_id: str | None = None,
        timing_window: str | None = None,
        actor: str | None = None,
    ) -> InterventionEvent:
        event = InterventionEvent(
            event_id=f"define-{intervention_id}",
            intervention_id=intervention_id,
            event_type="define",
            occurred_at=occurred_at,
            key=key,
            campaign_id=campaign_id,
            timing_window=timing_window,
            recommendation_id=recommendation_id,
            actor=actor,
        )
        self.append_event(event)
        return event


def reconstruct_interventions(
    events: Sequence[InterventionEvent],
    *,
    diagnostics: Sequence[InvalidEvent] = (),
) -> ReconstructionResult:
    ordered = sorted(enumerate(events), key=lambda item: (item[1].occurred_at.astimezone(timezone.utc), item[0]))
    snapshots: dict[str, InterventionSnapshot] = {}
    invalid_events: list[InvalidEvent] = list(diagnostics)
    checkpoint_ids_by_intervention: dict[str, list[str]] = defaultdict(list)
    seen_event_ids: set[str] = set()

    for _, event in ordered:
        if event.event_id in seen_event_ids:
            invalid_events.append(InvalidEvent(event=event.to_record(), reason="duplicate event_id"))
            continue
        seen_event_ids.add(event.event_id)
        reason = _apply_event(snapshots, checkpoint_ids_by_intervention, event)
        if reason is not None:
            invalid_events.append(InvalidEvent(event=event.to_record(), reason=reason))

    normalized: dict[str, InterventionSnapshot] = {}
    for intervention_id, snapshot in snapshots.items():
        normalized[intervention_id] = replace(
            snapshot,
            checkpoint_ids=tuple(checkpoint_ids_by_intervention.get(intervention_id, [])),
        )
    return ReconstructionResult(snapshots=normalized, invalid_events=tuple(invalid_events))


def active_intervention_guard(snapshots: Iterable[InterventionSnapshot], store_id: int) -> dict[str, Any]:
    active = [snapshot for snapshot in snapshots if snapshot.key.store_id == store_id and snapshot.is_active]
    if not active:
        return {"blocked": False, "status": "OK", "active_intervention_id": None}
    chosen = sorted(active, key=lambda snapshot: (snapshot.created_at, snapshot.intervention_id))[0]
    return {
        "blocked": True,
        "status": "ACTIVE_INTERVENTION",
        "active_intervention_id": chosen.intervention_id,
        "active_state": chosen.lifecycle_state,
    }


def exact_key_repetition_guard(snapshots: Iterable[InterventionSnapshot], key: InterventionKey) -> dict[str, Any]:
    matches = [snapshot for snapshot in snapshots if snapshot.key == key]
    if not matches:
        return {"blocked": False, "status": "OK", "matching_intervention_id": None}
    chosen = sorted(matches, key=lambda snapshot: (snapshot.created_at, snapshot.intervention_id))[0]
    return {
        "blocked": True,
        "status": "REPEATED_INTERVENTION",
        "matching_intervention_id": chosen.intervention_id,
        "matching_state": chosen.lifecycle_state,
    }


def resolve_project2_provenance(store_id: int, audit_runs: list[dict[str, Any]]) -> dict[str, Any]:
    first_run = first_run_for_store(store_id, audit_runs)
    if first_run is None:
        return {"campaign_id": None, "timing_window": None}
    raw_campaign = first_run.get("campaign_id") or first_run.get("campaign") or first_run.get("campaign_label")
    normalized_campaign = normalize_campaign_id(raw_campaign) if raw_campaign is not None else None

    raw_timing = first_run.get("timing_window") or first_run.get("timing")
    normalized_timing = canonical_timing_window(raw_timing) if raw_timing is not None else None

    return {
        "campaign_id": normalized_campaign if normalized_campaign is not None else raw_campaign,
        "timing_window": normalized_timing if normalized_timing is not None else raw_timing,
    }


# Transition table: event_type -> states from which the transition is valid.
# Terminal states not listed as a source simply fall through to "invalid".
_TRANSITION_SOURCES: dict[str, frozenset[str]] = {
    "approve": frozenset({RECOMMENDED}),
    "start": frozenset({APPROVED}),
    "pause": frozenset({ACTIVE}),
    "resume": frozenset({PAUSED}),
    "complete": frozenset({ACTIVE, PAUSED}),
    "fail": frozenset({ACTIVE, PAUSED}),
    "reject": frozenset({RECOMMENDED, APPROVED}),
    "expire": frozenset({RECOMMENDED, APPROVED}),
    "cancel": frozenset({APPROVED, ACTIVE, PAUSED}),
    "outcome_pending": frozenset({COMPLETED, FAILED}),
    "evaluate": frozenset({OUTCOME_PENDING}),
}
_TRANSITION_TARGET: dict[str, str] = {
    "approve": APPROVED,
    "start": ACTIVE,
    "pause": PAUSED,
    "resume": ACTIVE,
    "complete": COMPLETED,
    "fail": FAILED,
    "reject": REJECTED,
    "expire": EXPIRED,
    "cancel": CANCELLED,
    "outcome_pending": OUTCOME_PENDING,
    "evaluate": EVALUATED,
}
# Events that close the intervention and stamp ended_at.
_ENDING_EVENTS = frozenset({"complete", "fail", "reject", "expire", "cancel"})
# Events that may carry/refresh the outcome id.
_OUTCOME_EVENTS = frozenset({"outcome_pending", "evaluate"})
# States from which a checkpoint observation is still meaningful.
_CHECKPOINT_BLOCKED_STATES = frozenset({REJECTED, EXPIRED, CANCELLED, EVALUATED})


def _apply_event(
    snapshots: dict[str, InterventionSnapshot],
    checkpoint_ids_by_intervention: dict[str, list[str]],
    event: InterventionEvent,
) -> str | None:
    if event.event_type not in _VALID_EVENT_TYPES:
        return f"unsupported event_type {event.event_type}"
    if event.event_type == "define":
        return _define_intervention(snapshots, event)
    current = snapshots.get(event.intervention_id)
    if current is None:
        return "transition without intervention definition"
    identity_error = _validate_event_identity(event, current)
    if identity_error is not None:
        return identity_error
    if event.event_type == "checkpoint":
        return _apply_checkpoint(snapshots, checkpoint_ids_by_intervention, event, current)
    return _apply_transition(snapshots, event, current)


def _validate_event_identity(
    event: InterventionEvent, current: InterventionSnapshot
) -> str | None:
    if event.key != current.key:
        return "event InterventionKey conflicts with established intervention identity"
    if current.campaign_id is not None and event.campaign_id is not None and current.campaign_id != event.campaign_id:
        return "event campaign_id conflicts with established provenance"
    if current.timing_window is not None and event.timing_window is not None and current.timing_window != event.timing_window:
        return "event timing_window conflicts with established provenance"
    return None


def _define_intervention(
    snapshots: dict[str, InterventionSnapshot], event: InterventionEvent
) -> str | None:
    if event.intervention_id in snapshots:
        return "duplicate intervention definition"
    snapshots[event.intervention_id] = InterventionSnapshot(
        intervention_id=event.intervention_id,
        key=event.key,
        lifecycle_state=RECOMMENDED,
        recommendation_id=event.recommendation_id,
        approval_id=event.approval_id,
        campaign_id=event.campaign_id,
        timing_window=event.timing_window,
        created_at=event.occurred_at,
        updated_at=event.occurred_at,
    )
    return None


def _apply_checkpoint(
    snapshots: dict[str, InterventionSnapshot],
    checkpoint_ids_by_intervention: dict[str, list[str]],
    event: InterventionEvent,
    current: InterventionSnapshot,
) -> str | None:
    if current.lifecycle_state in _CHECKPOINT_BLOCKED_STATES:
        return f"invalid transition from {current.lifecycle_state} via {event.event_type}"
    if event.checkpoint_id is None:
        return "checkpoint event missing checkpoint_id"
    checkpoint_ids_by_intervention[event.intervention_id].append(event.checkpoint_id)
    snapshots[event.intervention_id] = replace(current, updated_at=event.occurred_at)
    return None


def _apply_transition(
    snapshots: dict[str, InterventionSnapshot],
    event: InterventionEvent,
    current: InterventionSnapshot,
) -> str | None:
    state = current.lifecycle_state
    valid_sources = _TRANSITION_SOURCES.get(event.event_type)
    if valid_sources is None or state not in valid_sources:
        return f"invalid transition from {state} via {event.event_type}"
    error = _validate_transition_preconditions(event, current)
    if error is not None:
        return error
    snapshots[event.intervention_id] = replace(
        current,
        lifecycle_state=_TRANSITION_TARGET[event.event_type],
        updated_at=event.occurred_at,
        **_transitioned_fields(event, current),
    )
    return None


def _validate_transition_preconditions(
    event: InterventionEvent, current: InterventionSnapshot
) -> str | None:
    if (
        event.event_type == "outcome_pending"
        and current.lifecycle_state == FAILED
        and current.execution_evidence is None
        and _execution_evidence(event) is None
    ):
        return "failed intervention lacks valid execution evidence"
    return None


def _transitioned_fields(
    event: InterventionEvent, current: InterventionSnapshot
) -> dict[str, Any]:
    """Per-event field updates; everything not touched here keeps its value."""
    event_type = event.event_type
    return {
        "recommendation_id": current.recommendation_id,
        "approval_id": (event.approval_id or current.approval_id) if event_type == "approve" else current.approval_id,
        "campaign_id": current.campaign_id if current.campaign_id is not None else event.campaign_id,
        "timing_window": current.timing_window if current.timing_window is not None else event.timing_window,
        "started_at": event.occurred_at if event_type == "start" else current.started_at,
        "ended_at": event.occurred_at if event_type in _ENDING_EVENTS else current.ended_at,
        "outcome_id": (event.outcome_id or current.outcome_id) if event_type in _OUTCOME_EVENTS else current.outcome_id,
        "execution_evidence": _transitioned_execution_evidence(event, current),
    }


def _transitioned_execution_evidence(
    event: InterventionEvent, current: InterventionSnapshot
) -> dict[str, Any] | None:
    if event.event_type == "fail":
        return _execution_evidence(event)
    if event.event_type == "outcome_pending":
        return current.execution_evidence or _execution_evidence(event)
    return current.execution_evidence


def _execution_evidence(event: InterventionEvent) -> dict[str, Any] | None:
    """MVP evidence marker; domain-specific execution validation is unresolved."""
    evidence = event.payload.get("execution_evidence")
    return evidence if isinstance(evidence, dict) and bool(evidence) else None
