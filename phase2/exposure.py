"""Read-only derived utility for store campaign eligibility and exposure share.

IMPORTANT DOMAIN DISTINCTION:
Dunnhumby campaign exposure was conducted via household direct mail (recorded in
``campaign_table.csv``), NOT direct store delivery. Store-level campaign metrics
are derived by aggregating the shopping activity of exposed households at each store
during the active campaign window.

This module computes that derived eligibility without fabricating direct store-level
campaign delivery records.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence


def compute_store_campaign_eligibility(
    *,
    store_id: int,
    transactions: Sequence[Mapping[str, Any]],
    campaign_households: set[str] | Sequence[str],
    start_day: int = 587,
    end_day: int = 642,
    min_exposed_revenue_share_pct: float = 50.0,
    min_exposed_households: int = 10,
) -> dict[str, Any]:
    """Calculate exposed-household revenue share and eligibility for a store.

    Parameters:
        store_id: Store identifier to evaluate.
        transactions: Sequence of transaction mappings containing at least:
            'STORE_ID' (or 'store_id'), 'DAY' (or 'day'),
            'household_key' (or 'household_id'), 'SALES_VALUE' (or 'sales_value').
        campaign_households: Set of household keys that received the campaign mailing.
        start_day: First day of the campaign window (inclusive, default 587 for Campaign 18).
        end_day: Last day of the campaign window (inclusive, default 642 for Campaign 18).
        min_exposed_revenue_share_pct: Threshold percentage for revenue eligibility.
        min_exposed_households: Threshold count of distinct exposed households.

    Returns:
        Dictionary containing derived store eligibility, revenue share, and methodology metadata.
    """
    if not isinstance(store_id, int) or isinstance(store_id, bool):
        raise TypeError("store_id must be an integer")
    if start_day > end_day:
        raise ValueError("start_day must not exceed end_day")

    hh_set = set(str(hh) for hh in campaign_households)
    total_store_sales = 0.0
    exposed_household_sales = 0.0
    exposed_households_seen: set[str] = set()
    total_transactions_count = 0
    exposed_transactions_count = 0

    for tx in transactions:
        sid = int(tx.get("STORE_ID", tx.get("store_id", -1)))
        if sid != store_id:
            continue

        day = int(tx.get("DAY", tx.get("day", -1)))
        if not (start_day <= day <= end_day):
            continue

        sales = float(tx.get("SALES_VALUE", tx.get("sales_value", 0.0)))
        hh = str(tx.get("household_key", tx.get("household_id", "")))

        total_store_sales += sales
        total_transactions_count += 1

        if hh in hh_set:
            exposed_household_sales += sales
            exposed_households_seen.add(hh)
            exposed_transactions_count += 1

    revenue_share_pct = (
        (exposed_household_sales / total_store_sales * 100)
        if total_store_sales > 0
        else 0.0
    )
    tx_share_pct = (
        (exposed_transactions_count / total_transactions_count * 100)
        if total_transactions_count > 0
        else 0.0
    )

    is_eligible = (
        revenue_share_pct >= min_exposed_revenue_share_pct
        or len(exposed_households_seen) >= min_exposed_households
    )

    return {
        "store_id": store_id,
        "derivation_type": "derived_store_eligibility",
        "direct_store_exposure_logged": False,
        "is_eligible": is_eligible,
        "campaign_window_days": f"Day {start_day} to Day {end_day}",
        "total_store_sales": round(total_store_sales, 2),
        "exposed_household_sales": round(exposed_household_sales, 2),
        "exposed_revenue_share_pct": round(revenue_share_pct, 2),
        "exposed_household_count": len(exposed_households_seen),
        "exposed_transaction_count": exposed_transactions_count,
        "total_transaction_count": total_transactions_count,
        "derivation_method": (
            "Aggregated transactions from campaign-exposed households (campaign_table.csv) "
            "at the store during the campaign window (campaign_desc.csv). "
            "Direct store campaign exposure is unrecorded in Dunnhumby source data."
        ),
        "source_tables": ["campaign_table.csv", "transaction_data.csv", "campaign_desc.csv"],
        "source_fields": ["household_key", "CAMPAIGN", "DAY", "STORE_ID", "SALES_VALUE"],
    }
