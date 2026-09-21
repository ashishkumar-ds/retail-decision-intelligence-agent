"""Append-only execution journal (execution/journal.py).

Durable record of every execution attempt and reversal. Same discipline as
``memory/history.py`` and ``approvals/ledger.py``: append-only, exclusive
file lock so concurrent workers cannot interleave partial lines, fsync before
unlock, never rewritten. Malformed lines are skipped on read and left in place.

Entry kinds:

- ``execution``  one applied (or dry-run) action, carrying the idempotency key
- ``reversal``   the undo of an earlier execution, referencing its id

The current state of an execution is derived by folding the two kinds in file
order (:func:`execution_state`), so the journal stays a pure event log.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("retail_decision_agent.execution")

DEFAULT_JOURNAL_PATH = Path("logs/execution_journal.jsonl")
_JOURNAL_PATH_ENV = "EXECUTION_JOURNAL_PATH"


def journal_path() -> Path:
    """Resolve the journal path at call time (tests and deployments relocate it)."""
    return Path(os.getenv(_JOURNAL_PATH_ENV, str(DEFAULT_JOURNAL_PATH)))


def append_event(entry: dict[str, Any]) -> dict[str, Any]:
    """Append one entry (locked + fsynced). Returns the entry as written."""
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as journal:
        fcntl.flock(journal.fileno(), fcntl.LOCK_EX)
        try:
            journal.write(json.dumps(entry, default=str) + "\n")
            journal.flush()
            os.fsync(journal.fileno())
        finally:
            fcntl.flock(journal.fileno(), fcntl.LOCK_UN)
    return entry


def read_events() -> list[dict[str, Any]]:
    """Read valid entries in file order (malformed/non-object lines are skipped)."""
    path = journal_path()
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as journal:
        for line_number, line in enumerate(journal, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Ignoring malformed execution entry at %s:%s", path, line_number)
                continue
            if isinstance(entry, dict):
                events.append(entry)
    return events
