"""Store-level retail context: margin proxy + availability proxy (P1+P2 data, no new infra).

Why this module exists: the per-store decision in ``decision_engine/scorer.py``
runs on forecast numbers only (``baseline/current_forecast``). It cannot tell
"customers aren't responding" (demand) from "the store had no coverage"
(supply/closure). True SKU-level inventory needs P1's 142MB
``transaction_data.csv`` + ``product.csv``, which are gitignored and not
fetchable here — so this module builds the strongest proxy available from
data that IS reachable:

- Margin proxy from P2 ``datasets/stores.csv`` (``SALES_VALUE, QUANTITY,
  RETAIL_DISC`` per store): ``discount_rate = -RETAIL_DISC / SALES_VALUE``,
  ``margin_rate = clamp(BASE_GROSS_MARGIN - discount_rate)``. Replaces the
  flat ``MARGIN_RATE = 0.25`` in ``phase2/budget_allocator.py`` with a
  store-specific rate when the caller supplies it.
- Availability proxy from the ``GET /actuals`` coverage the app already
  fetches: days without observed transactions are omitted by the API, so a
  recent-window coverage collapse against a healthy baseline is evidence of
  a possible supply/closure gap — not proof, hence ``POSSIBLE_SUPPLY_GAP``.

Contract (same discipline as the rest of the repo):

- Pure functions (``derive_store_margin``, ``assess_availability``,
  ``annotate_retail_context``) are deterministic and never touch the network.
- The loader (``get_store_retail_row``) is fail-open: any fetch/parse problem
  returns ``None``. The scorer treats ``None`` as "no context, decision
  unchanged" (backward compatible, golden cases stay green).
- The scorer NEVER flips a recommendation on retail context; it only annotates
  the reason, attaches the context block, and tempers confidence (``* 0.9``)
  on ``POSSIBLE_SUPPLY_GAP`` — same tempering precedent as the REVIEW_ZONE path.
"""
from __future__ import annotations

import csv
import logging
import os
from functools import lru_cache
from typing import Any, Mapping

logger = logging.getLogger("retail_decision_agent.retail_context")

# Same source P2's own service reads (see P2 ``main.py`` STORE_DATA_URL).
STORE_CONTEXT_URL = os.getenv(
    "RETAIL_STORE_CONTEXT_URL",
    "https://raw.githubusercontent.com/ashishkumar-ds/"
    "retail-campaign-automation-with-n8n/main/datasets/stores.csv",
)
STORE_CONTEXT_TIMEOUT_SECONDS = 10

# Assumed gross margin before discounts (grocery general merchandise).
# Net margin = base - discount_rate, clamped to [MIN, BASE]. The flat 0.25 in
# budget_allocator.py sits inside this band for a typical ~10% discount rate.
BASE_GROSS_MARGIN = 0.35
MIN_MARGIN_RATE = 0.05

# Recent-window coverage at/below this ratio with a healthy baseline behind it
# is flagged as a possible supply/closure gap rather than weak demand.
SUPPLY_GAP_RECENT_COVERAGE = 0.50
SUPPLY_GAP_BASELINE_COVERAGE = 0.70


def derive_store_margin(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Derive a store-specific margin proxy from a P2 stores.csv row.

    Returns ``None`` for missing/malformed rows (fail-open: caller keeps the
    flat default). Never raises for business-level absence of data.
    """
    if not isinstance(row, Mapping):
        return None
    try:
        sales = _as_float(row.get("SALES_VALUE", row.get("sales_value")))
        quantity = _as_float(row.get("QUANTITY", row.get("quantity")))
        discount = _as_float(row.get("RETAIL_DISC", row.get("retail_disc")), default=0.0)
    except (TypeError, ValueError):
        return None
    if sales is None or sales <= 0 or quantity is None or quantity <= 0:
        return None
    discount_rate = max(0.0, -discount / sales)
    margin_rate = min(BASE_GROSS_MARGIN, max(MIN_MARGIN_RATE, BASE_GROSS_MARGIN - discount_rate))
    return {
        "margin_rate": round(margin_rate, 4),
        "discount_rate": round(discount_rate, 4),
        "avg_unit_value": round(sales / quantity, 4),
        "methodology": (
            f"margin = clamp({BASE_GROSS_MARGIN} - discount_rate, "
            f"{MIN_MARGIN_RATE}, {BASE_GROSS_MARGIN}); "
            f"discount_rate = -RETAIL_DISC / SALES_VALUE from P2 stores.csv"
        ),
    }


def assess_availability(
    baseline_covered_days: int,
    baseline_expected_days: int,
    recent_covered_days: int,
    recent_expected_days: int,
) -> dict[str, Any]:
    """Classify recent coverage against its baseline (pure, deterministic).

    ``POSSIBLE_SUPPLY_GAP`` means: baseline coverage was healthy but the recent
    window collapsed — check ops/closure before spending more campaign budget.
    It is a flag for the human, never proof of a stockout.
    """
    for name, value in (
        ("baseline_covered_days", baseline_covered_days),
        ("baseline_expected_days", baseline_expected_days),
        ("recent_covered_days", recent_covered_days),
        ("recent_expected_days", recent_expected_days),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TypeError(f"{name} must be a non-negative integer")
    baseline_ratio = baseline_covered_days / baseline_expected_days if baseline_expected_days else 0.0
    recent_ratio = recent_covered_days / recent_expected_days if recent_expected_days else 0.0
    if baseline_expected_days == 0 or recent_expected_days == 0:
        state = "UNKNOWN"
    elif baseline_ratio >= SUPPLY_GAP_BASELINE_COVERAGE and recent_ratio <= SUPPLY_GAP_RECENT_COVERAGE:
        state = "POSSIBLE_SUPPLY_GAP"
    else:
        state = "OK"
    return {
        "availability_state": state,
        "baseline_coverage_ratio": round(baseline_ratio, 4),
        "recent_coverage_ratio": round(recent_ratio, 4),
    }


def annotate_retail_context(
    store_id: int,
    store_row: Mapping[str, Any] | None,
    availability: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Combine margin + availability into the scorer's retail-context block.

    Returns ``None`` when neither input carries usable data (scorer then leaves
    the recommendation byte-identical). Never raises for absent data.
    """
    if isinstance(store_id, bool) or not isinstance(store_id, int):
        raise TypeError("store_id must be an integer")
    margin = derive_store_margin(store_row) if store_row is not None else None
    avail_state = None
    if isinstance(availability, Mapping):
        avail_state = availability.get("availability_state")
    if margin is None and avail_state not in ("OK", "POSSIBLE_SUPPLY_GAP", "UNKNOWN"):
        return None
    demand_vs_supply = "UNKNOWN"
    ops_flag = None
    if avail_state == "POSSIBLE_SUPPLY_GAP":
        demand_vs_supply = "POSSIBLE_SUPPLY_GAP"
        ops_flag = (
            "Recent sales coverage collapsed against a healthy baseline — "
            "verify stock/closure with ops before extending campaign spend; "
            "low sales here may not be weak demand."
        )
    elif avail_state == "OK":
        demand_vs_supply = "DEMAND"
    return {
        "store_id": store_id,
        "margin_rate": margin["margin_rate"] if margin else None,
        "discount_rate": margin["discount_rate"] if margin else None,
        "avg_unit_value": margin["avg_unit_value"] if margin else None,
        "demand_vs_supply": demand_vs_supply,
        "availability": dict(availability) if isinstance(availability, Mapping) else None,
        "ops_flag": ops_flag,
    }


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric")
    try:
        return float(value)
    except (TypeError, ValueError):
        if default is not None:
            return default
        raise


@lru_cache(maxsize=1)
def _load_store_rows() -> dict[int, dict[str, Any]] | None:
    """Fetch + index P2 stores.csv once per process; ``None`` on any failure."""
    try:
        import httpx

        response = httpx.get(STORE_CONTEXT_URL, timeout=STORE_CONTEXT_TIMEOUT_SECONDS)
        response.raise_for_status()
        reader = csv.DictReader(response.text.splitlines())
        rows: dict[int, dict[str, Any]] = {}
        for raw in reader:
            lowered = {(k or "").strip().lower(): v for k, v in raw.items()}
            sid_raw = lowered.get("store_id")
            try:
                sid = int(str(sid_raw).strip())
            except (TypeError, ValueError):
                continue
            rows[sid] = {
                "SALES_VALUE": lowered.get("sales_value"),
                "QUANTITY": lowered.get("quantity"),
                "RETAIL_DISC": lowered.get("retail_disc", 0),
            }
        return rows
    except Exception as error:
        logger.warning("[RETAIL CONTEXT] store table unavailable: %s: %s",
                       type(error).__name__, error)
        return None


def get_store_retail_row(store_id: int) -> dict[str, Any] | None:
    """Best-effort P2 row for one store; ``None`` when unavailable (fail-open)."""
    if isinstance(store_id, bool) or not isinstance(store_id, int):
        raise TypeError("store_id must be an integer")
    rows = _load_store_rows()
    if not rows:
        return None
    return rows.get(store_id)
