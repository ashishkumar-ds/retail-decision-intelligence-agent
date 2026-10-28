"""Test suite for real-data validation and portfolio evaluation contracts."""

import pytest

from phase2.portfolio import evaluate_store_portfolio

pytestmark = pytest.mark.real_data


def test_real_data_validation_contracts_with_mocked_forecast(monkeypatch):
    # Mock stores metadata
    monkeypatch.setattr(
        "phase2.portfolio.get_all_stores_info",
        lambda: {
            31642: {"store_id": 31642, "last_day": 711},
            317: {"store_id": 317, "last_day": 711},
            99999: {"store_id": 99999, "last_day": 711},
        },
    )
    
    # Mock evaluation window forecast
    def fake_eval_forecast(store_id, start_day, window_start_offset=46, window_end_offset=59):
        if store_id == 31642:
            return 86.55
        if store_id == 317:
            return 56.24
        raise ValueError(f"No mock forecast for store {store_id}")

    monkeypatch.setattr("phase2.portfolio.get_evaluation_window_forecast", fake_eval_forecast)

    # Dense transactions meeting the SUFFICIENT coverage bar (28 baseline + 7 recent per store)
    transactions = []
    for day in range(531, 559):  # 28 baseline days in [531, 587)
        transactions.append({"STORE_ID": 31642, "DAY": day, "household_key": "H1", "SALES_VALUE": 70.0})
        transactions.append({"STORE_ID": 317, "DAY": day, "household_key": "H2", "SALES_VALUE": 100.0})
    for day in range(633, 640):  # 7 recent days in [633, 647)
        transactions.append({"STORE_ID": 31642, "DAY": day, "household_key": "H1", "SALES_VALUE": 77.0})
        transactions.append({"STORE_ID": 317, "DAY": day, "household_key": "H2", "SALES_VALUE": 50.0})

    report = evaluate_store_portfolio(
        store_ids=[31642, 317, 88888, 99999], # 88888 not in forecast API (NO_DATA), 99999 triggers error (ERROR)
        transactions=transactions,
        campaign_households={"H1", "H2"},
        campaign_start_day=587,
        campaign_end_day=642,
        auto_forecast=True,
    )

    assert report.total_stores == 4
    
    # Store 31642: baseline $70, recent $77, forecast $86.55
    res_31642 = next(r for r in report.store_results if r.store_id == 31642)
    assert res_31642.forecast_status == "AVAILABLE"
    assert res_31642.baseline_daily_mean == pytest.approx(70.0)
    assert res_31642.recent_daily_mean == pytest.approx(77.0)
    assert res_31642.forecast_reference_value == pytest.approx(86.55)
    assert res_31642.longitudinal_uplift_pct == pytest.approx(10.0)
    assert res_31642.counterfactual_uplift_pct == pytest.approx((77.0 - 86.55) / 86.55 * 100)
    assert res_31642.join_state == "SUFFICIENT"

    # Store 88888: NO_DATA state
    res_88888 = next(r for r in report.store_results if r.store_id == 88888)
    assert res_88888.forecast_status == "NO_DATA"
    assert res_88888.forecast_reference_value is None
    assert res_88888.counterfactual_uplift_pct is None

    # Store 99999: ERROR state
    res_99999 = next(r for r in report.store_results if r.store_id == 99999)
    assert res_99999.forecast_status == "ERROR"
    assert res_99999.forecast_reference_value is None
    assert res_99999.counterfactual_uplift_pct is None


def test_portfolio_recent_window_is_fourteen_days_half_open(monkeypatch):
    """Regression: the portfolio recent window is [start+46, start+60).

    Day 647 (offset +60) is the evaluation due instant and must not be folded
    into the recent daily mean, which would make the window 15 days wide.
    """
    monkeypatch.setattr(
        "phase2.portfolio.get_all_stores_info",
        lambda: {31642: {"store_id": 31642, "last_day": 711}},
    )
    monkeypatch.setattr(
        "phase2.portfolio.get_evaluation_window_forecast",
        lambda store_id, start_day, window_start_offset=46, window_end_offset=59: 86.55,
    )

    transactions = []
    for day in range(531, 559):  # 28 baseline days in [531, 587)
        transactions.append({"STORE_ID": 31642, "DAY": day, "household_key": "H1", "SALES_VALUE": 70.0})
    for day in range(633, 647):  # all 14 in-window recent days [633, 647)
        transactions.append({"STORE_ID": 31642, "DAY": day, "household_key": "H1", "SALES_VALUE": 77.0})
    # Poisoned boundary sample on day 647 (offset +60): must be excluded.
    transactions.append({"STORE_ID": 31642, "DAY": 647, "household_key": "H1", "SALES_VALUE": 1000.0})

    report = evaluate_store_portfolio(
        store_ids=[31642],
        transactions=transactions,
        campaign_households={"H1"},
        campaign_start_day=587,
        campaign_end_day=642,
        auto_forecast=True,
    )

    res = next(r for r in report.store_results if r.store_id == 31642)
    assert res.join_state == "SUFFICIENT"
    assert res.recent_daily_mean == pytest.approx(77.0)
