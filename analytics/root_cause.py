"""Root-cause tagging over the decision log (analytics/root_cause.py).

Read-only, off-path analytics: every persisted recommendation carries a
grounded ``reason``; the board can show *what is actually wrong across the
estate* by tagging those reasons into closed vocabularies. Uses
classifier.dev's **dimensions** surface (Jev) - one call, several dimensions,
each with its own labels and instructions.

Three hard rules, in the same spirit as the rest of the repo:

- **Never on the decision path.** Tags are analytics. Nothing here writes to
  the recommendation log, the ledger or the execution journal, and no
  decision reads a tag.
- **Numbers are redacted before egress.** ``reason`` text contains business
  figures ("health score 95.0", "recovery 9.0 percent", "Store 317"). The
  classifier needs semantics, not magnitudes, so digits, amounts, percentages
  and store ids are stripped before the text leaves the process: no commercial
  figures reach a third party, and classification quality is unaffected.
- **Fail-open.** Any failure (network, quota, malformed payload) returns an
  ``unavailable`` status with empty counts - a board must never fail because
  an analytics call did.

The vocabularies are ours and closed; each includes "none of these" so the
model always has a legitimate way out (the documented missing-category trap:
without it every item is forced into the nearest label).
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from typing import Any, Iterable, Mapping, Sequence

logger = logging.getLogger("retail_decision_agent.root_cause")

ENDPOINT = "https://classifier.dev/v1/classify"
TIER_ENV = "ROOT_CAUSE_TIER"
MAX_ITEMS = 200
USER_AGENT = "retail-decision-intelligence-agent/1.0 (analytics-root-cause)"

# Closed vocabularies. Keep them small: a long label list makes the model
# spread probability across near-synonyms and returns noisier tags.
ROOT_CAUSE_LABELS = (
    "traffic decline",
    "basket size or conversion decline",
    "stockout or availability problem",
    "pricing or promotion issue",
    "campaign fatigue or timing",
    "staffing or store operations",
    "customer retention or churn",
    "data quality or instrumentation gap",
    "seasonality or external factor",
    "none of these",
)
DRIVER_LABELS = (
    "demand-side",
    "supply-side",
    "execution or process",
    "measurement or data",
    "external",
    "none of these",
)

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|percent|k|m)?", re.IGNORECASE)
_STORE_RE = re.compile(r"\bstore\s+\d+\b", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def redact_numbers(text: str) -> str:
    """Strip figures and store ids before text leaves the process."""
    redacted = _STORE_RE.sub("store [id]", str(text or ""))
    redacted = _NUMBER_RE.sub("[n]", redacted)
    return _WS_RE.sub(" ", redacted).strip()


def _item_text(record: Mapping[str, Any]) -> str:
    """One classifier input: semantics only, no magnitudes, no store ids."""
    outcome = record.get("outcome_evidence") or {}
    assessment = outcome.get("target_assessment") or "unmeasured"
    return (
        f"RECOMMENDATION: {record.get('recommendation')}. "
        f"REASON: {redact_numbers(str(record.get('reason') or ''))}. "
        f"MEASURED_OUTCOME: {assessment}."
    )


def _post(body: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "user-agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _tier() -> str:
    tier = os.getenv(TIER_ENV, "fast").strip().lower()
    return tier if tier in ("fast", "smart") else "fast"


def classify_reasons(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Tag up to ``MAX_ITEMS`` records; returns one entry per input, in order.

    Uses the dimensions contract: ``dimensions`` must not be combined with
    ``labels``/``multi`` in one request, so this never sends them.
    """
    items = [_item_text(record) for record in records[:MAX_ITEMS]]
    if not items:
        return []
    payload = _post({
        "items": items,
        "tier": _tier(),
        "dimensions": {
            "root_cause": {
                "labels": list(ROOT_CAUSE_LABELS),
                "instructions": (
                    "The primary reason this retail store is underperforming, "
                    "judging only from the recommendation and its stated reason."
                ),
            },
            "driver": {
                "labels": list(DRIVER_LABELS),
                "instructions": "Which side of the business the cause sits on.",
            },
        },
    })
    return list(payload.get("results") or [])


def tag_recommendations(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate root-cause tags for the board. Never raises.

    Returns ``counts`` (root cause -> stores), ``driver_counts``,
    ``by_store`` (store id -> tags) and a ``status`` naming either the tier
    used or the failure that degraded the section.
    """
    candidates = [r for r in records
                  if r.get("reason") and r.get("recommendation")
                  and isinstance(r.get("store_id"), int)]
    empty = {"status": "no records to tag", "tagged": 0, "counts": {},
             "driver_counts": {}, "by_store": {}}
    if not candidates:
        return empty
    batch = candidates[:MAX_ITEMS]
    try:
        results = classify_reasons(candidates)
        if len(results) != len(batch):
            raise ValueError("classifier results do not match input count")
    except Exception as error:
        logger.warning("[ROOT CAUSE] tagging unavailable: %s: %s",
                       type(error).__name__, error)
        return {**empty, "status": f"unavailable ({type(error).__name__})"}

    counts = {label: 0 for label in ROOT_CAUSE_LABELS}
    driver_counts = {label: 0 for label in DRIVER_LABELS}
    by_store: dict[int, dict[str, Any]] = {}
    store_ids: Iterable[int] = (r["store_id"] for r in batch)
    for store_id, result in zip(store_ids, results):
        dimensions = result.get("dimensions") or {}
        root = dimensions.get("root_cause") or {}
        driver = dimensions.get("driver") or {}
        root_label = root.get("label")
        driver_label = driver.get("label")
        if root_label in counts:
            counts[root_label] += 1
        if driver_label in driver_counts:
            driver_counts[driver_label] += 1
        by_store[store_id] = {
            "root_cause": root_label,
            "root_cause_confidence": root.get("confidence"),  # may be null
            "driver": driver_label,
            "model": root.get("model"),
        }
    return {
        "status": f"tagged ({_tier()} tier)",
        "tagged": len(by_store),
        "counts": {label: n for label, n in counts.items() if n},
        "driver_counts": {label: n for label, n in driver_counts.items() if n},
        "by_store": by_store,
    }
