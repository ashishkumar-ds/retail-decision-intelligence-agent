"""Read-only adapter for Project 2's campaign execution audit log.

This module reads Project 2's audit data without importing or reproducing
Project 2 campaign logic. Its optional HTTP source is limited to ``GET /audit``.

External contract validation is split: envelope shape and per-run field-set /
error-message checks are performed here (their messages are part of this
adapter's tested surface), while per-run field types are enforced by the
pydantic schema in ``phase2.schemas.AuditRunPayload`` (single home for the
contract). The contract is strict on the four fields Project 3 consumes and
tolerant of extra upstream metadata: Project 2 may annotate a run with its own
operational fields, and only the four consumed fields are copied into the
normalized read model - nothing upstream is trusted into decision logic.
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_API_URL = "https://retail-campaign-automation.onrender.com/audit"
REQUEST_TIMEOUT_SECONDS = 10
DEFAULT_RETRY_BACKOFFS = (2.0, 4.0, 8.0)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
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

    Rationale: campaign identity in the underlying Dunnhumby ground truth
    (campaign_desc.csv, campaign_table.csv) is always a plain integer, with
    no duplicate or alternate-format campaign IDs across either table -
    verified against the full 30-campaign source data. Project 2's free-text
    'campaign' label is a rendering of that same integer, not an
    independently-assigned identifier. That justifies deriving campaign_id
    from a cleanly-matching label, but it is still a derived value, not an
    originally-stable one: callers get campaign_provenance_status=NORMALIZED
    rather than treating it as equivalent to a directly-provided campaign_id.
    Project 3 still has no formal contract with Project 2 guaranteeing this
    labeling convention holds indefinitely, so this function fails closed
    (returns None) for any label that doesn't cleanly match rather than
    guessing.
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


def ping_upstream(timeout_seconds: float = 8.0) -> bool:
    """Best-effort wake-up ping for the campaign-audit upstream (never raises).

    Render free-tier sleeps after ~15 min idle; a dashboard hit fires this in
    a daemon thread so the click that warms our service also warms the store
    universe behind it. Read-only GET, short timeout, any failure is just
    logged — the board serves cached records either way.
    """
    url = _audit_api_url() or DEFAULT_AUDIT_API_URL
    try:
        response = httpx.get(url, timeout=timeout_seconds)
        return response.status_code < 500
    except Exception as error:  # cold start in progress, DNS, timeout — all fine
        logger.info("[CAMPAIGN AUDIT WARM-UP] ping %s: %s: %s",
                    url, type(error).__name__, error)
        return False


def _is_retryable_error(error: Exception) -> bool:
    """Whether an error is transient: a cold start or a network failure.

    A 4xx that is not 429 is a wiring/config problem (wrong path, revoked
    access); retrying cannot fix it, so the caller must see it immediately.
    """
    if isinstance(error, (httpx.TransportError, httpx.TimeoutException, TimeoutError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        return status_code is None or status_code in RETRYABLE_STATUS_CODES
    return False


def _request_with_retry(
    method: str,
    url: str,
    *,
    retries: int = 3,
    backoffs: Sequence[float] = DEFAULT_RETRY_BACKOFFS,
    sleep_fn: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> httpx.Response:
    """Execute a request, retrying transient failures with exponential backoff.

    Project 2 runs on a tier that spins down when idle, so the first read after
    a quiet period can time out or answer 5xx while the instance boots. Same
    policy as ``tools/forecast_tool.py``: retry those, never retry a non-429
    4xx, and let the caller see the final error rather than inventing data.
    """
    kwargs.setdefault("timeout", REQUEST_TIMEOUT_SECONDS)
    last_error: Exception | None = None
    req_func = getattr(httpx, method.lower(), httpx.request)

    for attempt in range(retries + 1):
        try:
            if req_func is httpx.request:
                response = req_func(method, url, **kwargs)
            else:
                response = req_func(url, **kwargs)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return response
        except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError,
                TimeoutError) as error:
            last_error = error
            if not _is_retryable_error(error):
                logger.warning(
                    "[CAMPAIGN AUDIT CLIENT ERROR] non-retryable error for %s %s: %s: %s",
                    method, url, type(error).__name__, error)
                raise
            if attempt < retries:
                backoff = backoffs[attempt] if attempt < len(backoffs) else backoffs[-1]
                logger.warning(
                    "[CAMPAIGN AUDIT RETRY] attempt %s/%s for %s %s failed with %s: %s. "
                    "Retrying in %ss...", attempt + 1, retries, method, url,
                    type(error).__name__, error, backoff)
                sleep_fn(backoff)
            else:
                logger.error(
                    "[CAMPAIGN AUDIT EXHAUSTED] all %s attempts for %s %s failed. "
                    "Last error: %s: %s", retries + 1, method, url,
                    type(error).__name__, error)

    if last_error:
        raise last_error
    raise httpx.HTTPError(f"Failed to execute {method} {url}")


def get_audit_log(
    *,
    retries: int = 3,
    backoffs: Sequence[float] = DEFAULT_RETRY_BACKOFFS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Return audit-run objects from the opt-in API or configured JSONL file.

    An absent configuration or missing local file is a genuine no-data state
    and returns an empty list. Local malformed/non-object lines are skipped
    with a warning; the source file is never changed.
    """
    api_url = _audit_api_url()
    if api_url is not None:
        return _get_audit_log_from_api(api_url, retries=retries, backoffs=backoffs,
                                       sleep_fn=sleep_fn)

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


def _get_audit_log_from_api(
    api_url: str,
    *,
    retries: int = 3,
    backoffs: Sequence[float] = DEFAULT_RETRY_BACKOFFS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Fetch and normalize Project 2's read-only ``GET /audit`` response.

    The API is used only when ``CAMPAIGN_AUDIT_API_URL`` is configured. Transient
    failures (a cold start's 5xx/timeout) are retried with backoff; HTTP failures
    that persist propagate as ``httpx`` errors and bad payloads raise
    ``CampaignAuditResponseError``; neither case is silently converted to
    campaign data. No campaign execution endpoint is called.
    """
    _validate_audit_api_url(api_url)
    response = _request_with_retry("GET", api_url, retries=retries, backoffs=backoffs,
                                   sleep_fn=sleep_fn)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
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
    # Envelope validation: pydantic for the typed contract, with the
    # adapter's own messages preserved for the failure surfaces tests pin.
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
        # Missing consumed fields are fatal (we cannot evaluate without them).
        # Unexpected EXTRA fields are tolerated: Project 2 annotates runs with
        # its own operational metadata (phase, rollout_decision, benchmark_*)
        # and Project 3 has no frozen field-set contract with it, so an upstream
        # addition must not become our ingestion outage. Only _AUDIT_RUN_FIELDS
        # are validated and copied into the normalized record below - extra
        # fields never reach decision logic.
        missing_fields = _AUDIT_RUN_FIELDS - set(run)
        if missing_fields:
            raise CampaignAuditResponseError(
                f"campaign audit API run {index} has invalid fields; "
                f"missing={sorted(missing_fields)}"
            )
        # Field types/values are enforced by the pydantic contract schema.
        # Imported lazily: phase2/__init__ imports evaluator, which imports
        # this module - a module-level import here would be circular.
        from phase2.schemas import AuditRunPayload

        try:
            AuditRunPayload.model_validate(run)
        except ValidationError as error:
            raise CampaignAuditResponseError(
                f"campaign audit API run {index} has invalid fields: {error.errors()[0]['msg']}"
            ) from error
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
