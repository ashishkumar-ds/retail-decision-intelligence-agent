"""Shared ISO-8601 timestamp parsing helpers (app/_time.py).

One small module for the near-duplicate parsers that used to live inline in
``app/main.py`` (``_parse_moment`` / ``_iso_day`` / the generated-at reader):
every call site needs the same contract, so a parsing behavior change can
never silently apply to one surface and not another.

Reading convention, matching how the rest of the service stamps timestamps:

- Absent / non-string / malformed input parses to ``None`` — callers skip the
  record instead of crashing a read surface over one bad line.
- ``Z`` suffix is accepted (wire format), naive timestamps are read as UTC
  (the service and its logs are UTC-only); the result is always aware.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

__all__ = ["iso_day", "parse_moment"]


def parse_moment(value: Any) -> datetime | None:
    """Aware datetime of an ISO-8601 timestamp, or None when absent/malformed."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def iso_day(value: Any) -> date | None:
    """Calendar day of an ISO-8601 timestamp, or None when absent/malformed."""
    moment = parse_moment(value)
    return moment.date() if moment is not None else None
