"""Execution layer — turning an approved recommendation into an action
(execution/connector.py).

Closes the ``execute`` stage of plan → execute → measure → re-decide. Before
this module, an approval wrote a ledger entry and nothing acted on it; the
loop only *looked* closed.

Contract (same discipline as the rest of the repo):

- **Human gate first.** Only an approved, approval-gated recommendation may be
  executed (:func:`execute_recommendation` refuses anything else with
  :class:`ExecutionRefused`). Execution never invents a decision.
- **Idempotent.** The idempotency key is derived from
  ``store_id + recommendation_id + recommendation``. Re-executing the same
  approval returns the existing execution and appends nothing — retries,
  double-clicks and worker races cannot double-apply an action.
- **Reversible.** Every execution can be undone (:func:`reverse_execution`),
  recorded as a ``reversal`` event in the append-only journal.
- **Auditable.** Each attempt lands in ``execution/journal.py``; that journal,
  not this module, is the durable state.

``DryRunConnector`` is the default: it performs no external write and records
what *would* have happened, so the whole loop — gate, idempotency, reversal,
measurement — is exercisable and testable without a vendor account. A real
adapter implements the same two methods:

    ponytail: ceiling = the default connector writes nothing outside the
    journal. Upgrade path = implement ``apply``/``reverse`` against a real
    system (POS/CRM/email) and inject it at the endpoint; nothing else in the
    loop changes, because gating, idempotency, reversal and audit live here
    rather than in the adapter.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from approvals.ledger import utcnow_iso
from guardrails import APPROVAL_REQUIRED_RECOMMENDATIONS

from .journal import append_event, read_events


class ExecutionRefused(ValueError):
    """The record may not be executed (unapproved or not an actionable gate)."""


class ExecutionNotFound(KeyError):
    """No execution with that id in the journal."""


class ExecutionAlreadyReversed(ValueError):
    """That execution has already been reversed."""


class ExecutionConnector(Protocol):
    """The seam a real system adapter implements. Two methods, nothing else."""

    name: str

    def apply(self, intent: Mapping[str, Any]) -> str:
        """Perform the action; return an opaque handle for the audit trail."""

    def reverse(self, handle: str) -> None:
        """Undo the action identified by ``handle``."""


@dataclass(frozen=True)
class DryRunConnector:
    """Records intent without touching any external system (the default)."""

    name: str = "dry-run"

    def apply(self, intent: Mapping[str, Any]) -> str:
        return f"dry-run:{intent.get('idempotency_key', 'unknown')}"

    def reverse(self, handle: str) -> None:
        return None


def idempotency_key(record: Mapping[str, Any]) -> str:
    """Stable key: same approval -> same key -> at most one live execution."""
    material = "{}:{}:{}".format(
        record.get("store_id"),
        record.get("recommendation_id"),
        record.get("recommendation"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def execution_state(events: Sequence[Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Fold journal events into ``execution_id -> current execution state``.

    Reversals mark their target ``reversed_at``; reversals naming an unknown
    execution (truncated or hand-edited journal) are ignored, never invented.
    """
    events = read_events() if events is None else events
    state: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event") == "execution":
            state[str(event["execution_id"])] = dict(event)
        elif event.get("event") == "reversal":
            target = state.get(str(event.get("execution_id")))
            if target is not None:
                target["reversed_at"] = event.get("reversed_at")
                target["reversal_id"] = event.get("reversal_id")
    return state


def _active_execution(key: str) -> dict[str, Any] | None:
    for record in execution_state().values():
        if record.get("idempotency_key") == key and not record.get("reversed_at"):
            return record
    return None


def _require_gated_approval(record: Mapping[str, Any]) -> None:
    if record.get("approved") is not True or not record.get("decided_at"):
        raise ExecutionRefused(
            "recommendation is not approved: only a human-approved, decided "
            "recommendation may be executed")
    if record.get("recommendation") not in APPROVAL_REQUIRED_RECOMMENDATIONS:
        raise ExecutionRefused(
            f"recommendation {record.get('recommendation')!r} is not an "
            "approval-gated action; there is nothing to execute")


def execute_recommendation(record: Mapping[str, Any],
                           connector: ExecutionConnector | None = None,
                           actor: str | None = None) -> tuple[dict[str, Any], bool]:
    """Execute an approved recommendation. Returns ``(execution, created)``.

    ``created`` is False on the idempotent path (an identical live execution
    already existed and nothing was appended). Raises :class:`ExecutionRefused`
    for anything ungated.
    """
    _require_gated_approval(record)
    connector = connector or DryRunConnector()
    key = idempotency_key(record)
    existing = _active_execution(key)
    if existing is not None:
        return existing, False

    handle = connector.apply({
        "store_id": record.get("store_id"),
        "recommendation_id": record.get("recommendation_id"),
        "recommendation": record.get("recommendation"),
        "approval_id": record.get("approval_id"),
        "idempotency_key": key,
        "connector": connector.name,
    })
    execution = {
        "event": "execution",
        "execution_id": f"exec-{uuid.uuid4().hex}",
        "idempotency_key": key,
        "connector": connector.name,
        "connector_handle": handle,
        "store_id": record.get("store_id"),
        "recommendation_id": record.get("recommendation_id"),
        "recommendation": record.get("recommendation"),
        "approval_id": record.get("approval_id"),
        "actor": actor,
        "created_at": utcnow_iso(),
        "reversed_at": None,
        "reversal_id": None,
    }
    append_event(execution)
    return execution, True


def reverse_execution(execution_id: str,
                      connector: ExecutionConnector | None = None,
                      actor: str | None = None) -> dict[str, Any]:
    """Undo an execution; returns the updated execution state."""
    connector = connector or DryRunConnector()
    current = execution_state().get(execution_id)
    if current is None:
        raise ExecutionNotFound(execution_id)
    if current.get("reversed_at"):
        raise ExecutionAlreadyReversed(execution_id)

    connector.reverse(str(current.get("connector_handle") or ""))
    append_event({
        "event": "reversal",
        "reversal_id": f"rev-{uuid.uuid4().hex}",
        "execution_id": execution_id,
        "connector": current.get("connector"),
        "actor": actor,
        "reversed_at": utcnow_iso(),
    })
    return execution_state()[execution_id]
