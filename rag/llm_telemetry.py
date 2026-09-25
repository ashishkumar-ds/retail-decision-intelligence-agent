"""Append-only telemetry for the off-path LLM layers (measurement, never influence).

Why this exists: the LLM layers (narrative rephrasing, advisory triage,
retrieval pre-filter) sit off the decision path, and their safety property is
enforced by *guards*, not by prompt text. An unmeasured guard is a liability:
nobody can tell whether the layer is being vetoed constantly (bad model, drifted
prompt, substituted provider) or silently degrading to deterministic output (a
dead provider, a missing key). Both are invisible in a healthy-looking service.

This module records one append-only line per off-path LLM *attempt*, so that:

- ``/metrics`` can expose guard-rejection and degradation counts per layer,
  computed on scrape from this file like every other series (no scrape state);
- an operator has an audit trail for off-path behaviour that is deliberately
  kept OUT of the recommendation log, approval ledger and execution journal,
  which stay reserved for decisions (ADR-0002).

Discipline (same as the rest of the repo):

- NEVER on the decision path, and never read by it: a missing, empty or corrupt
  telemetry file changes no recommendation, no approval, no execution.
- Append-only, exclusive lock + fsync (mirrors ``memory/history.py``).
  Malformed lines are skipped and left in place.
- Recording never raises: telemetry must not be able to break a request.
- Only *attempts* are recorded. A layer that is switched off writes nothing;
  ``/health`` already reports flag state.
- Metric labels carry counts and reason names only - never business figures.

Env: ``OFFPATH_LLM_LOG_PATH`` relocates the file (tests and the eval harness
point it at a temporary path so synthetic drafts never enter the real trail).
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

logger = logging.getLogger("retail_decision_agent.llm_telemetry")

TELEMETRY_PATH_ENV = "OFFPATH_LLM_LOG_PATH"
DEFAULT_TELEMETRY_PATH = Path("logs/offpath_llm.jsonl")

# Outcomes (what the layer did with the attempt).
OUTCOME_SERVED = "served"                     # grounded LLM content was served
OUTCOME_GUARD_REJECTED = "guard_rejected"     # a gate vetoed the draft (fail-closed)
OUTCOME_UNAVAILABLE = "unavailable"           # provider/parse failure, template served

# Reasons (which gate vetoed, or why the draft was unusable).
REASON_NUMERIC = "numeric"
REASON_CITATION = "citation"
REASON_LEXICON = "lexicon"
REASON_EMPTY = "empty"
REASON_PARSE = "parse"                        # untyped failure (e.g. advisory parse)

LAYERS = ("explainer", "advisory", "prefilter")

_MAX_DETAIL_CHARS = 300


def telemetry_path() -> Path:
    """Resolve the telemetry file at call time (tests relocate it)."""
    return Path(os.getenv(TELEMETRY_PATH_ENV, str(DEFAULT_TELEMETRY_PATH)))

def record(layer: str, outcome: str, *, reason: str = "",
           latency_ms: float | None = None, detail: str = "",
           counts: Mapping[str, int] | None = None) -> None:
    """Append one telemetry event. Never raises - telemetry cannot break a request."""
    try:
        event: dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "layer": layer,
            "outcome": outcome,
        }
        if reason:
            event["reason"] = reason
        if latency_ms is not None:
            event["latency_ms"] = round(float(latency_ms), 1)
        if counts:
            event["counts"] = {str(key): int(value) for key, value in counts.items()}
        if detail:
            event["detail"] = " ".join(str(detail).split())[:_MAX_DETAIL_CHARS]
        path = telemetry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, default=str) + "\n"
        with path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception as error:  # noqa: BLE001 - telemetry must never raise
        logger.warning("[LLM TELEMETRY] dropped event (%s): %s: %s",
                       layer, type(error).__name__, error)


def read_events() -> list[dict[str, Any]]:
    """Read valid telemetry events, skipping (and preserving) malformed lines."""
    path = telemetry_path()
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Ignoring malformed telemetry record at %s:%s", path, line_number)
                continue
            if isinstance(event, dict):
                events.append(event)
            else:
                logger.warning("Ignoring non-object telemetry record at %s:%s", path, line_number)
    return events


def summarise(events: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Aggregate telemetry for the metrics endpoint (counts only, no figures)."""
    events = read_events() if events is None else list(events)
    outcomes: dict[tuple[str, str], int] = {}
    reasons: dict[tuple[str, str], int] = {}
    counts_by_key: dict[tuple[str, str], int] = {}
    latency_totals: dict[str, float] = {}
    latency_counts: dict[str, int] = {}
    for event in events:
        layer = str(event.get("layer") or "unknown")
        outcome = str(event.get("outcome") or "unknown")
        outcomes[(layer, outcome)] = outcomes.get((layer, outcome), 0) + 1
        reason = event.get("reason")
        if reason:
            reasons[(layer, str(reason))] = reasons.get((layer, str(reason)), 0) + 1
        for key, value in (event.get("counts") or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                counts_by_key[(layer, str(key))] = counts_by_key.get((layer, str(key)), 0) + int(value)
        latency = event.get("latency_ms")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool):
            latency_totals[layer] = latency_totals.get(layer, 0.0) + float(latency)
            latency_counts[layer] = latency_counts.get(layer, 0) + 1
    return {
        "total": len(events),
        "outcomes": outcomes,
        "reasons": reasons,
        "counts": counts_by_key,
        "latency_ms_avg": {
            layer: latency_totals[layer] / latency_counts[layer]
            for layer in sorted(latency_totals)
        },
    }
