"""Read-only adapter for Project 2's campaign execution audit log.

This module reads Project 2's audit data without importing or reproducing
Project 2 campaign logic. Its optional HTTP source is limited to ``GET /audit``.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_API_URL = "https://retail-campaign-automation.onrender.com/audit"
REQUEST_TIMEOUT_SECONDS = 10
MISSING_STABLE_CAMPAIGN_ID = "MISSING_STABLE_CAMPAIGN_ID"

# This is intentionally a small, documented read model. In particular, Project
# 2's human-facing ``campaign`` value is a label, not a Project 3-defined
# external identifier. Execution and rollout fields are not audit evidence of
# delivery and are therefore not part of the contract.
_AUDIT_RUN_FIELDS = frozenset({"campaign", "timing", "run_timestamp", "store_ids"})


def normalize_campaign_id(value: Any) -> str | None:
    """Deterministically map campaign labels to stable integer string identifiers.

    Maps 'Campaign 18', 'campaign-18', '18', 18 -> '18'.
    Returns None for non-standard labels (e.g. 'campaign-api-1', 'Summer Promo')
    or empty inputs without guessing.
    """
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    match = re.match(r"^(?:campaign\s*[-_]?\s*)?(\d+)$", cleaned, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def canonical_timing_window(value: Any) -> str | None:
    """Standardize timing window representation across Project 2 and Project 3.

    Normalizes common afternoon representations ('12 PM - 6 PM', '12:00-18:00',
    '12-18', 'afternoon') to the canonical '12 PM - 6 PM' while preserving
    other valid non-empty timing strings.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    lowered = cleaned.lower().replace(" ", "").replace(":", "")
    if lowered in {"12pm-6pm", "1200-1800", "12-18", "afternoon", "12pm–6pm", "1200-1759"}:
        return "12 PM - 6 PM"
    return cleaned


class CampaignAuditResponseError(ValueError):
    """The configured campaign audit API returned unusable data."""


def _audit_log_path() -> Path | None:
    configured = os.getenv("CAMPAIGN_AUDIT_LOG_PATH")
    return Path(configured) if configured else None


def _audit_api_url() -> str | None:
    """Return the explicitly configured read-only audit endpoint, if any."""
    configured = os.getenv("CAMPAIGN_AUDIT_API_URL")
    return configured.strip() if configured and configured.strip() else None


def get_audit_log() -> list[dict[str, Any]]:
    """Return audit-run objects from the opt-in API or configured JSONL file.

    An absent configuration or missing local file is a genuine no-data state
    and returns an empty list. Local malformed/non-object lines are skipped
    with a warning; the source file is never changed.
    """
    api_url = _audit_api_url()
    if api_url is not None:
        return _get_audit_log_from_api(api_url)

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


def _get_audit_log_from_api(api_url: str) -> list[dict[str, Any]]:
    """Fetch and normalize Project 2's read-only ``GET /audit`` response.

    The API is used only when ``CAMPAIGN_AUDIT_API_URL`` is configured. HTTP
    failures propagate as ``requests`` errors and bad payloads raise
    ``CampaignAuditResponseError``; neither case is silently converted to
    campaign data. No campaign execution endpoint is called.
    """
    _validate_audit_api_url(api_url)
    response = requests.get(api_url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    try:
        payload = response.json()
    except (ValueError, requests.JSONDecodeError) as error:
        raise CampaignAuditResponseError("campaign audit API returned invalid JSON") from error
    return _normalise_api_audit_response(payload)


def _normalise_api_audit_response(payload: Any) -> list[dict[str, Any]]:
    """Validate Project 2's read-only audit envelope and run schema.

    The external ``campaign`` field remains a display label. Project 3 has no
    contract defining it (for example, ``Campaign 18``) as a stable external
    campaign ID, so every API record explicitly reports that provenance gap.
    """
    if not isinstance(payload, dict):
        raise CampaignAuditResponseError("campaign audit API response must be an object")
    total_runs = payload.get("total_runs")
    runs = payload.get("runs")
    if not isinstance(total_runs, int) or isinstance(total_runs, bool):
        raise CampaignAuditResponseError("campaign audit API response total_runs must be an integer")
    if not isinstance(runs, list):
        raise CampaignAuditResponseError("campaign audit API response runs must be a list")
    if total_runs != len(runs):
        raise CampaignAuditResponseError("campaign audit API response total_runs does not match runs")

    normalised: list[dict[str, Any]] = []
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            raise CampaignAuditResponseError(f"campaign audit API run {index} must be an object")
        unexpected_fields = set(run) - _AUDIT_RUN_FIELDS
        missing_fields = _AUDIT_RUN_FIELDS - set(run)
        if missing_fields or unexpected_fields:
            raise CampaignAuditResponseError(
                f"campaign audit API run {index} has invalid fields; "
                f"missing={sorted(missing_fields)}, unexpected={sorted(unexpected_fields)}"
            )
        campaign_label = _required_nonempty_text(run["campaign"], "campaign", index)
        campaign_id = normalize_campaign_id(campaign_label)
        provenance_status = "NORMALIZED" if campaign_id is not None else MISSING_STABLE_CAMPAIGN_ID
        timing_window = canonical_timing_window(_required_nonempty_text(run["timing"], "timing", index))
        run_timestamp = _required_timestamp(run["run_timestamp"], index)
        store_ids = _required_store_ids(run["store_ids"], index)
        normalised.append({
            "campaign_label": campaign_label,
            "campaign_id": campaign_id,
            "campaign_provenance_status": provenance_status,
            "timing_window": timing_window,
            "run_timestamp": run_timestamp,
            "store_ids": store_ids,
        })
    return normalised


def _validate_audit_api_url(api_url: str) -> None:
    """Reject configured URLs that are not an HTTP(S) ``/audit`` endpoint."""
    parsed = urlparse(api_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CampaignAuditResponseError("campaign audit API URL must be an absolute HTTP(S) URL")
    if parsed.path.rstrip("/") != "/audit":
        raise CampaignAuditResponseError("campaign audit API URL must target /audit")


def _required_nonempty_text(value: Any, field: str, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CampaignAuditResponseError(
            f"campaign audit API run {index} {field} must be a non-empty string"
        )
    return value


def _required_timestamp(value: Any, index: int) -> str:
    if not isinstance(value, str) or _parse_timestamp(value) is None:
        raise CampaignAuditResponseError(
            f"campaign audit API run {index} run_timestamp must be ISO-8601 with a timezone"
        )
    return value


def _required_store_ids(value: Any, index: int) -> list[int]:
    if not isinstance(value, list) or not value:
        raise CampaignAuditResponseError(f"campaign audit API run {index} store_ids must be a non-empty list")
    if any(not isinstance(store_id, int) or isinstance(store_id, bool) or store_id <= 0 for store_id in value):
        raise CampaignAuditResponseError(
            f"campaign audit API run {index} store_ids must contain only positive integers"
        )
    if len(set(value)) != len(value):
        raise CampaignAuditResponseError(f"campaign audit API run {index} store_ids must not contain duplicates")
    return value


def _normalised_store_ids(run: dict[str, Any]) -> list[int]:
    store_ids = run.get("store_ids", [])
    if not isinstance(store_ids, list):
        return []
    return [store_id for store_id in store_ids if isinstance(store_id, int) and not isinstance(store_id, bool)]


def _parse_timestamp(value: Any) -> datetime | None:
    """Return an aware UTC timestamp, or ``None`` for an invalid value."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _parse_run_timestamp(run: dict[str, Any]) -> datetime | None:
    """Return an aware UTC timestamp, or ``None`` for invalid audit data."""
    return _parse_timestamp(run.get("run_timestamp"))


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
