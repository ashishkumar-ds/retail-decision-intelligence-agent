"""Pydantic schemas for external HTTP contracts.

Runtime-enforced contracts for the two external data envelopes this service
consumes (Project 2's campaign audit API and the forecast actuals feed) and
for the outcome evaluation request body. These complement the frozen
dataclasses in ``phase2/contracts.py``: dataclasses model the internal
event-sourced domain; pydantic models validate data crossing a process
boundary (HTTP responses and request bodies) fail-closed.

Error mapping convention: callers convert ``pydantic.ValidationError`` (a
``ValueError`` subclass) into their existing transport-specific error types,
so error surfaces and HTTP status codes are unchanged.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Strict(BaseModel):
    """Forbid unknown fields on strict external contracts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class AuditRunPayload(BaseModel):
    """One campaign audit run from Project 2's ``GET /audit`` response.

    Strict on the fields Project 3 consumes, tolerant of everything else.

    This mirrors the documented 4-field read model in
    ``tools/campaign_tool.py`` (``_AUDIT_RUN_FIELDS``); ``scripts/check.py``
    asserts the two stay in sync. ``extra="ignore"`` is deliberate and is the
    difference from ``_Strict``: Project 3 is a read-only consumer of an
    endpoint it does not own, with no frozen field-set contract. The live
    service annotates each run with operational metadata (``phase``,
    ``rollout_decision``, ``benchmark_sales_uplift``, ``target_segment``, ...)
    - rejecting an upstream *addition* would turn their improvement into our
    outage, so unknown fields are ignored rather than fatal.

    The read model itself is unchanged: only these 4 fields are validated and
    only these 4 are copied into the normalized record, so upstream metrics
    (notably the superseded ``benchmark_sales_uplift`` forecast) can never
    enter Project 3's decision logic.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)

    campaign: str
    timing: str
    run_timestamp: str
    store_ids: list[int]

    @field_validator("campaign", "timing")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value

    @field_validator("store_ids")
    @classmethod
    def _positive_unique_ids(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("store_ids must be a non-empty list")
        if any(isinstance(s, bool) or s <= 0 for s in value):
            raise ValueError("store_ids must contain only positive integers")
        if len(set(value)) != len(value):
            raise ValueError("store_ids must not contain duplicates")
        return value


class AuditEnvelopePayload(_Strict):
    """Project 2's ``GET /audit`` response envelope."""

    total_runs: int
    runs: list[AuditRunPayload]

    @field_validator("runs")
    @classmethod
    def _total_matches(cls, value: list[AuditRunPayload], info) -> list[AuditRunPayload]:
        total = info.data.get("total_runs")
        if total is not None and total != len(value):
            raise ValueError("total_runs does not match runs")
        return value


class ActualsRow(BaseModel):
    """One observed-sales day from the forecast actuals feed."""

    model_config = ConfigDict(extra="ignore")

    day: int
    sales_value: float
    date: str | None = None


class ActualsEnvelope(BaseModel):
    """``GET /actuals/{store_id}`` response envelope."""

    model_config = ConfigDict(extra="ignore")

    start_day: int | None = None
    end_day: int | None = None
    observations: list[ActualsRow] = Field(default_factory=list)


class OutcomeRequestBody(BaseModel):
    """Validated request body for ``POST /phase2/interventions/{id}/outcome``.

    Extra fields are allowed (forward compatibility); known fields are
    strictly typed so malformed bodies are rejected with 400 before any
    evaluation logic runs.
    """

    model_config = ConfigDict(extra="allow")

    started_day: int | None = None
    observations: list[dict[str, Any]] = Field(default_factory=list)
    outcome_id: str | None = None
    as_of: str | None = None
    forecast_status: str | None = None
    auto_controls: bool = False
