"""Append-only intervention registry and deterministic replay helpers."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools.campaign_tool import first_run_for_store

from .contracts import (
    ACTIVE,
    ACTIVE_INTERVENTION_STATES,
    APPROVED,
    CANCELLED,
    COMPLETED,
    EVALUATED,
    EXPIRED,
    FAILED,
    InterventionEvent,
    InterventionKey,
    InterventionLifecycleState,
    InterventionSnapshot,
    InvalidEvent,
    OUTCOME_PENDING,
    PAUSED,
    RECOMMENDED,
    REJECTED,
    ReconstructionResult,
    TERMINAL_INTERVENTION_STATES,
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_record(), default=str, ensure_ascii=False) + "\n")

    def read_events(self) -> list[InterventionEvent]:
        if not self.path.exists():
            return []
        events: list[InterventionEvent] = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                try:
                    events.append(InterventionEvent.from_record(record))
                except (KeyError, TypeError, ValueError):
                    continue
        return events

    def reconstruct(self) -> ReconstructionResult:
        return reconstruct_interventions(self.read_events())

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


def reconstruct_interventions(events: Sequence[InterventionEvent]) -> ReconstructionResult:
    ordered = sorted(enumerate(events), key=lambda item: (item[1].occurred_at.astimezone(timezone.utc), item[0]))
    snapshots: dict[str, InterventionSnapshot] = {}
    invalid_events: list[InvalidEvent] = []
    checkpoint_ids_by_intervention: dict[str, list[str]] = defaultdict(list)

    for _, event in ordered:
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
    return {
        "campaign_id": first_run.get("campaign_id"),
        "timing_window": first_run.get("timing_window"),
    }


def _apply_event(
    snapshots: dict[str, InterventionSnapshot],
    checkpoint_ids_by_intervention: dict[str, list[str]],
    event: InterventionEvent,
) -> str | None:
    if event.event_type not in _VALID_EVENT_TYPES:
        return f"unsupported event_type {event.event_type}"

    current = snapshots.get(event.intervention_id)
    if event.event_type == "define":
        if current is not None:
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

    if current is None:
        return "transition without intervention definition"

    state = current.lifecycle_state
    new_state = state
    started_at = current.started_at
    ended_at = current.ended_at
    recommendation_id = current.recommendation_id
    approval_id = current.approval_id
    campaign_id = current.campaign_id if current.campaign_id is not None else event.campaign_id
    timing_window = current.timing_window if current.timing_window is not None else event.timing_window
    outcome_id = current.outcome_id

    if event.event_type == "approve" and state == RECOMMENDED:
        new_state = APPROVED
        approval_id = event.approval_id or approval_id
    elif event.event_type == "start" and state == APPROVED:
        new_state = ACTIVE
        started_at = event.occurred_at
    elif event.event_type == "pause" and state == ACTIVE:
        new_state = PAUSED
    elif event.event_type == "resume" and state == PAUSED:
        new_state = ACTIVE
    elif event.event_type == "complete" and state in {ACTIVE, PAUSED}:
        new_state = COMPLETED
        ended_at = event.occurred_at
    elif event.event_type == "fail" and state in {ACTIVE, PAUSED}:
        new_state = FAILED
        ended_at = event.occurred_at
    elif event.event_type == "reject" and state in {RECOMMENDED, APPROVED}:
        new_state = REJECTED
        ended_at = event.occurred_at
    elif event.event_type == "expire" and state in {RECOMMENDED, APPROVED}:
        new_state = EXPIRED
        ended_at = event.occurred_at
    elif event.event_type == "cancel" and state in {APPROVED, ACTIVE, PAUSED}:
        new_state = CANCELLED
        ended_at = event.occurred_at
    elif event.event_type == "outcome_pending" and state in {COMPLETED, FAILED}:
        new_state = OUTCOME_PENDING
        outcome_id = event.outcome_id or outcome_id
    elif event.event_type == "evaluate" and state == OUTCOME_PENDING:
        new_state = EVALUATED
        outcome_id = event.outcome_id or outcome_id
    elif event.event_type == "checkpoint" and state not in {REJECTED, EXPIRED, CANCELLED, EVALUATED}:
        if event.checkpoint_id is None:
            return "checkpoint event missing checkpoint_id"
        checkpoint_ids_by_intervention[event.intervention_id].append(event.checkpoint_id)
        snapshots[event.intervention_id] = replace(current, updated_at=event.occurred_at)
        return None
    else:
        return f"invalid transition from {state} via {event.event_type}"

    snapshots[event.intervention_id] = replace(
        current,
        lifecycle_state=new_state,
        recommendation_id=recommendation_id,
        approval_id=approval_id,
        campaign_id=campaign_id,
        timing_window=timing_window,
        started_at=started_at,
        ended_at=ended_at,
        outcome_id=outcome_id,
        updated_at=event.occurred_at,
    )
    return None
