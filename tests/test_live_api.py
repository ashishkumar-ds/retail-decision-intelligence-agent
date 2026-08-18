"""Live API tests: hit the deployed external services over the network.

These tests are network-dependent and are excluded from the default run
(see ``pytest.ini``: ``addopts = -m "not live_api"``). Run them explicitly
with ``pytest -m live_api`` against the live Forecast API
(``FORECAST_API_URL``) and, when configured, the live Campaign Audit API
(``CAMPAIGN_AUDIT_API_URL``).
"""
import math
import os

import pytest

from phase2.portfolio import evaluate_store_portfolio
from tools import campaign_tool, forecast_tool

pytestmark = pytest.mark.live_api


def test_live_forecast_api_stores_contract():
    """The deployed Forecast API serves a non-empty, schema-valid store list."""
    stores = forecast_tool.get_all_stores_info()
    assert stores, "live Forecast API returned no stores"
    for store_id, meta in stores.items():
        assert isinstance(store_id, int)
        assert isinstance(meta.get("last_day"), int)


def test_live_forecast_api_prediction_and_window_contract():
    """A live store yields finite, positive per-day and 14-day window forecasts."""
    stores = forecast_tool.get_all_stores_info()
    assert stores, "live Forecast API returned no stores"
    store_id = next(iter(stores))
    last_day = stores[store_id]["last_day"]
    assert last_day >= 60, f"store {store_id} last_day {last_day} predates evaluation window"

    daily = forecast_tool.get_prediction(store_id, last_day)
    assert math.isfinite(daily) and daily > 0

    window = forecast_tool.get_evaluation_window_forecast(
        store_id=store_id,
        start_day=last_day - 60,
        window_start_offset=47,
        window_end_offset=60,
    )
    assert math.isfinite(window) and window > 0


def test_live_forecast_api_portfolio_evaluation():
    """Portfolio evaluation against the live Forecast API stays contract-clean.

    Uses synthetic, real-shaped transaction fixtures for a few live stores so
    the assertions mirror the ``real_data`` integrity checks (no dummy
    substitution, unit scale consistency, dual-metric independence, provenance
    joins) while the forecast reference is fetched live.
    """
    stores = forecast_tool.get_all_stores_info()
    assert stores, "live Forecast API returned no stores"
    store_ids = list(stores)[:3]

    txs = []
    for sid in store_ids:
        for d in range(531, 587):
            txs.append({"STORE_ID": sid, "DAY": d, "SALES_VALUE": 50.0, "household_key": f"hh-{sid}"})
        for d in range(634, 648):
            txs.append({"STORE_ID": sid, "DAY": d, "SALES_VALUE": 60.0, "household_key": f"hh-{sid}"})

    report = evaluate_store_portfolio(
        store_ids=store_ids,
        transactions=txs,
        campaign_households={f"hh-{sid}" for sid in store_ids},
        campaign_start_day=587,
        campaign_end_day=642,
        auto_forecast=True,
    )

    assert report.total_stores == len(store_ids)
    for res in report.store_results:
        assert res.forecast_status in {"AVAILABLE", "NO_DATA", "ERROR"}
        if res.forecast_status == "AVAILABLE":
            assert res.forecast_reference_value is not None
            assert res.counterfactual_uplift_pct is not None
            if res.recent_daily_mean:
                ratio = res.forecast_reference_value / res.recent_daily_mean
                assert 0.05 <= ratio <= 20.0, f"store {res.store_id} unit scale discrepancy"
        else:
            assert res.forecast_reference_value is None
            assert res.counterfactual_uplift_pct is None
        if res.evidence_state == "SUFFICIENT":
            assert res.join_state == "SUFFICIENT", f"store {res.store_id} join mismatch"


def test_live_campaign_audit_api_readonly_contract():
    """When configured, the live Campaign Audit API is reachable and schema-valid."""
    configured = os.getenv("CAMPAIGN_AUDIT_API_URL")
    if not configured:
        pytest.skip("CAMPAIGN_AUDIT_API_URL not configured; campaign audit live test skipped")
    runs = campaign_tool.get_audit_log()
    assert isinstance(runs, list)
    for run in runs:
        assert run["campaign_id"] is None
        assert run["campaign_provenance_status"] == campaign_tool.MISSING_STABLE_CAMPAIGN_ID
        assert isinstance(run["store_ids"], list)