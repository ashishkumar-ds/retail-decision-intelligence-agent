"""Append-only JSONL recommendation log.

Malformed lines are ignored by :func:`read_log` so one corrupted entry does
not prevent valid historical entries from being read. They are logged as a
warning and are never overwritten or removed.

Appends take an exclusive file lock so concurrent workers (multiple uvicorn
threads/processes) cannot interleave partial JSON lines.
"""
import fcntl
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
DEFAULT_LOG_PATH = Path("logs/recommendation_log.jsonl")


def _log_path() -> Path:
    return Path(os.getenv("RECOMMENDATION_LOG_PATH", str(DEFAULT_LOG_PATH)))


def append_log(record: dict[str, Any]) -> None:
    """Append one JSON-serializable record to the configured JSONL log (locked)."""
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    with path.open("a", encoding="utf-8") as log_file:
        fcntl.flock(log_file.fileno(), fcntl.LOCK_EX)
        try:
            log_file.write(line)
            log_file.flush()
            os.fsync(log_file.fileno())
        finally:
            fcntl.flock(log_file.fileno(), fcntl.LOCK_UN)


def read_log() -> list[dict[str, Any]]:
    """Read valid object records from the configured JSONL log, if present."""
    path = _log_path()
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as log_file:
        for line_number, line in enumerate(log_file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Ignoring malformed JSONL record at %s:%s", path, line_number)
                continue
            if not isinstance(record, dict):
                logger.warning("Ignoring non-object JSONL record at %s:%s", path, line_number)
                continue
            records.append(record)
    return records
