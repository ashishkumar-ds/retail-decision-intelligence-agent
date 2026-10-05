"""Store×SKU context — the extension point for true inventory-aware decisions.

Status: interface + fail-open loader + proxy logic, WITHOUT the 142MB P1
``transaction_data.csv`` / ``product.csv`` (gitignored, manual dunnhumby
download). Until a rollup lands at ``RETAIL_SKU_ROLLUP_PATH``, every function
degrades to ``None`` and the decision path is byte-identical.

Rollup schema (CSV, header row required):

    STORE_ID,SKU,DEPARTMENT,units_56d,sales_value_56d,units_14d,on_hand

- ``units_56d``: baseline-window units (velocity anchor).
- ``units_14d``: recent-window units; 0 with positive baseline velocity and
  ``on_hand == 0`` (or missing) is a suspected stockout, not weak demand.
- ``on_hand`` may be omitted entirely — then any zero-recent SKU with baseline
  velocity is flagged ``UNVERIFIED`` (possible gap, explicitly not proven).

``annotate_sku_context`` returns a block the scorer merges into
``retail_context["sku"]``: suspected stockout SKUs (top sellers first),
coverage, and an upgraded ``demand_vs_supply`` verdict. Never raises for
absent data; raises ``TypeError``/``ValueError`` only for caller contract
violations (bad types, negative units).
"""
from __future__ import annotations

import csv
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

logger = logging.getLogger("retail_decision_agent.sku_context")

SKU_ROLLUP_PATH_ENV = "RETAIL_SKU_ROLLUP_PATH"
REQUIRED_COLUMNS = {"store_id", "sku", "units_56d", "sales_value_56d", "units_14d"}

# A top-decile seller (by baseline sales) at zero recent units triggers the gap.
TOP_SELLER_FRACTION = 0.10
MIN_BASELINE_UNITS = 5


def _norm_row(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    lowered = {(str(k) or "").strip().lower(): v for k, v in raw.items()}
    if not REQUIRED_COLUMNS <= set(lowered):
        return None
    try:
        sid = int(str(lowered["store_id"]).strip())
        units_base = float(lowered["units_56d"])
        units_recent = float(lowered["units_14d"])
        sales_base = float(lowered["sales_value_56d"])
    except (TypeError, ValueError):
        return None
    if isinstance(sid, bool) or sid <= 0 or units_base < 0 or units_recent < 0:
        return None
    on_hand_raw = lowered.get("on_hand")
    try:
        on_hand = None if on_hand_raw in (None, "") else float(on_hand_raw)
    except (TypeError, ValueError):
        on_hand = None
    return {
        "store_id": sid,
        "sku": str(lowered["sku"]),
        "department": str(lowered.get("department") or ""),
        "units_56d": units_base,
        "sales_value_56d": sales_base,
        "units_14d": units_recent,
        "on_hand": on_hand,
    }


def load_sku_rollup(path: str | Path | None = None) -> dict[int, list[dict[str, Any]]] | None:
    """Load the store×SKU rollup; ``None`` when unconfigured, missing or unreadable."""
    resolved = path or os.getenv(SKU_ROLLUP_PATH_ENV)
    if not resolved:
        return None
    try:
        with open(resolved, encoding="utf-8", newline="") as handle:
            rows = [_norm_row(raw) for raw in csv.DictReader(handle)]
    except (OSError, ValueError) as error:
        logger.warning("[SKU CONTEXT] rollup unreadable at %s: %s: %s",
                       resolved, type(error).__name__, error)
        return None
    by_store: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        if row is not None:
            by_store.setdefault(row["store_id"], []).append(row)
    return by_store or None


@lru_cache(maxsize=1)
def _cached_rollup(resolved: str) -> dict[int, list[dict[str, Any]]] | None:
    return load_sku_rollup(resolved)


def get_store_skus(store_id: int) -> list[dict[str, Any]] | None:
    """Best-effort SKU rows for one store; ``None`` when unavailable (fail-open)."""
    if isinstance(store_id, bool) or not isinstance(store_id, int):
        raise TypeError("store_id must be an integer")
    resolved = os.getenv(SKU_ROLLUP_PATH_ENV)
    if not resolved:
        return None
    by_store = _cached_rollup(resolved)
    if not by_store:
        return None
    return by_store.get(store_id)


def flag_sku_gaps(skus: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Flag suspected stockouts: baseline velocity, zero recent units.

    ``confirmed`` needs ``on_hand == 0``; otherwise ``unverified`` (possible
    gap, explicitly not proven). Top sellers are SKUs in the top decile by
    baseline sales value.
    """
    skus = [s for s in skus if isinstance(s, Mapping)]
    if not skus:
        return {"suspected_stockouts": [], "unverified_gaps": [],
                "sku_count": 0, "coverage_ratio": None}
    ordered = sorted(skus, key=lambda s: float(s.get("sales_value_56d") or 0.0), reverse=True)
    top_n = max(1, int(len(ordered) * TOP_SELLER_FRACTION))
    top = set(id(s) for s in ordered[:top_n])
    suspected, unverified = [], []
    covered = 0
    for sku in skus:
        base = float(sku.get("units_56d") or 0.0)
        recent = float(sku.get("units_14d") or 0.0)
        if recent > 0:
            covered += 1
            continue
        if base < MIN_BASELINE_UNITS:
            continue
        entry = {"sku": sku.get("sku"), "department": sku.get("department"),
                 "units_56d": base, "top_seller": id(sku) in top,
                 "on_hand": sku.get("on_hand")}
        if sku.get("on_hand") == 0:
            suspected.append(entry)
        else:
            unverified.append(entry)
    return {
        "suspected_stockouts": sorted(suspected, key=lambda e: (-e["units_56d"], str(e["sku"]))),
        "unverified_gaps": sorted(unverified, key=lambda e: (-e["units_56d"], str(e["sku"]))),
        "sku_count": len(skus),
        "coverage_ratio": round(covered / len(skus), 4),
    }


def annotate_sku_context(store_id: int, skus: Sequence[Mapping[str, Any]] | None) -> dict[str, Any] | None:
    """SKU block for ``retail_context["sku"]``; ``None`` when no SKU data."""
    if isinstance(store_id, bool) or not isinstance(store_id, int):
        raise TypeError("store_id must be an integer")
    if not skus:
        return None
    gaps = flag_sku_gaps(skus)
    top_hit = any(e["top_seller"] for e in gaps["suspected_stockouts"] + gaps["unverified_gaps"])
    if gaps["suspected_stockouts"] or top_hit:
        verdict = "POSSIBLE_SUPPLY_GAP"
    elif gaps["unverified_gaps"]:
        verdict = "POSSIBLE_SUPPLY_GAP"
    else:
        verdict = "DEMAND"
    return {
        "store_id": store_id,
        "demand_vs_supply": verdict,
        "sku_count": gaps["sku_count"],
        "sku_coverage_ratio": gaps["coverage_ratio"],
        "suspected_stockout_skus": gaps["suspected_stockouts"][:10],
        "unverified_gap_skus": gaps["unverified_gaps"][:10],
        "provenance": "store×SKU rollup (RETAIL_SKU_ROLLUP_PATH); top-decile sellers weighted",
    }
