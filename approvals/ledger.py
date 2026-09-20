"""Append-only decision ledger with a double gate on approve/reject.

Merchant-agent `changes.py` pattern adapted: guardrails are checked when a
recommendation is *created* (scorer/verifier, before persistence) and again
when a human *decides* it. Every decision is appended to a durable JSONL
ledger with the gate result, so the audit trail shows not just what was
decided but that the decision-time safety checks ran and passed.

The ledger is evidence only: it never mutates the recommendation log
(``memory/history.py`` remains the system of record for recommendations).

Rebuild invariant: pending state lives in the durable SQLite store
(``app/state.py``), which is backfilled at first start from the
recommendation log only (``app/main._rebuild_pending_approvals``); the ledger
is never consulted for pending state. The ledger write happens BEFORE the
recommendation-log write inside the same request and raises on gate failure,
so a ledger entry is always accompanied by its decided record - a decision
that exists in the ledger but not in the log would indicate tampering and
should be treated as an audit incident.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decision_engine.verifier import verify_recommendation
from guardrails import requires_human_approval

logger = logging.getLogger(__name__)
DEFAULT_LEDGER_PATH = Path("logs/approval_ledger.jsonl")


def _ledger_path() -> Path:
    return Path(os.getenv("APPROVAL_LEDGER_PATH", str(DEFAULT_LEDGER_PATH)))


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def decision_gate(record: dict[str, Any]) -> dict[str, Any]:
    """The decision-time double gate (merchant-agent: guardrails at stage AND apply).

    Re-runs, at approval time:
    1. the guardrail policy: the recommendation must still be one that
       requires human approval (a rule-set change between recommendation
       and decision must not retroactively legitimise an ungated action);
    2. the deterministic verifier over the full record.

    Returns ``{"allowed": bool, "checks": {...}}``. Callers must fail closed
    (refuse the decision) when ``allowed`` is False.
    """
    recommendation = record.get("recommendation")
    guardrail_ok = requires_human_approval(recommendation) is True
    verification = verify_recommendation(record)
    checks = {
        "still_requires_human_approval": guardrail_ok,
        "decision_time_verification_passed": verification["passed"],
    }
    return {"allowed": all(checks.values()), "checks": checks, "verification": verification}


def append_decision(record: dict[str, Any], decision: str, actor: str | None,
                    gate: dict[str, Any], decided_by: str | None = None) -> dict[str, Any]:
    """Append one gate-stamped decision to the ledger (locked, fail-closed).

    Raises ``ValueError`` if the gate did not allow the decision - callers
    convert that into their HTTP surface, the ledger is never written with
    an ungated approval.
    """
    if not gate.get("allowed"):
        raise ValueError(f"decision gate failed: {gate.get('checks')}")
    entry = {
        "decision_id": f"decision-{uuid.uuid4().hex}",
        "store_id": record.get("store_id"),
        "recommendation_id": record.get("recommendation_id"),
        "recommendation": record.get("recommendation"),
        "decision": decision,
        "actor": actor,
        "decided_by": decided_by,  # authenticated principal (identity.py)
        "decided_at": utcnow_iso(),
        "gate": {"allowed": True, "checks": gate.get("checks")},
    }
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as ledger_file:
        fcntl.flock(ledger_file.fileno(), fcntl.LOCK_EX)
        try:
            ledger_file.write(json.dumps(entry, default=str) + "\n")
            ledger_file.flush()
            os.fsync(ledger_file.fileno())
        finally:
            fcntl.flock(ledger_file.fileno(), fcntl.LOCK_UN)
    return entry


def read_decisions() -> list[dict[str, Any]]:
    """Read valid ledger entries (malformed lines are skipped, never repaired)."""
    path = _ledger_path()
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as ledger_file:
        for line_number, line in enumerate(ledger_file, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Ignoring malformed ledger entry at %s:%s", path, line_number)
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries
