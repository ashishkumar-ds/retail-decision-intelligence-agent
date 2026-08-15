"""Read-only adapter for Project 2's campaign execution audit log.

This module deliberately reads Project 2's ``audit_log.jsonl`` directly and
does not import or reproduce Project 2 campaign logic.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _audit_log_path() -> Path | None:
    configured = os.getenv("CAMPAIGN_AUDIT_LOG_PATH")
    return Path(configured) if configured else None


def get_audit_log() -> list[dict[str, Any]]:
    """Return valid audit-run objects from the configured Project 2 JSONL file.

    An absent configuration or missing file is a genuine no-data state and
    returns an empty list. Malformed/non-object lines are skipped with a
    warning; the source file is never changed.
    """
    path = _audit_log_path()
    if path is None or not path.exists():
        return []

    runs: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as audit_file:
        for line_number, line in enumerate(audit_file, start=1):
            if not line.strip():
                continue
            try:
                run = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Ignoring malformed campaign audit line at %s:%s", path, line_number)
                continue
            if not isinstance(run, dict):
                logger.warning("Ignoring non-object campaign audit line at %s:%s", path, line_number)
                continue
            runs.append(run)
    return runs


def _normalised_store_ids(run: dict[str, Any]) -> list[int]:
    store_ids = run.get("store_ids", [])
    if not isinstance(store_ids, list):
        return []
    return [store_id for store_id in store_ids if isinstance(store_id, int) and not isinstance(store_id, bool)]



def _parse_run_timestamp(run: dict[str, Any]) -> datetime | None:
    """Return an aware UTC timestamp, or ``None`` for invalid audit data."""
    value = run.get("run_timestamp")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)

def get_store_ids_from_audit_log(audit_runs: list[dict[str, Any]]) -> list[int]:
    """Return unique store IDs in first-seen order across campaign runs."""
    seen: set[int] = set()
    store_ids: list[int] = []
    for run in audit_runs:
        for store_id in _normalised_store_ids(run):
            if store_id not in seen:
                seen.add(store_id)
                store_ids.append(store_id)
    return store_ids


def first_run_for_store(store_id: int, audit_runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the chronologically earliest valid UTC-aware audit run for a store.

    Malformed or timezone-naive timestamps are ignored with a warning. The
    original audit record is returned unchanged only after timestamp validation;
    ties retain audit-file order deterministically.
    """
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for run in audit_runs:
        if store_id not in _normalised_store_ids(run):
            continue
        parsed = _parse_run_timestamp(run)
        if parsed is None:
            logger.warning("Ignoring campaign audit run with invalid run_timestamp for store %s", store_id)
            continue
        candidates.append((parsed, run))
    return min(candidates, key=lambda candidate: candidate[0])[1] if candidates else None
