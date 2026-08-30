"""Smoke tests against the real, deployed Forecast API and Campaign Audit API.

Excluded from the default suite (pytest.ini: addopts = -m "not live_api").
Run explicitly once both services are confirmed reachable:

    pytest -m live_api -v

These are intentionally light-touch: they confirm connectivity, response
shape, and basic contract compliance, not specific business values (which
depend on whatever data the live services happen to hold right now). Keep
it that way - asserting on specific numbers here would make this suite
flaky against a live, changing backend.
"""
import os
import time

import pytest
import requests

from tools import campaign_tool, forecast_tool

pytestmark = pytest.mark.live_api


def _require_campaign_audit_api_configured():
    if not os.getenv("CAMPAIGN_AUDIT_API_URL"):
        pytest.skip(
            "CAMPAIGN_AUDIT_API_URL not set - this test exercises the live "
            "Campaign Audit API specifically, not the local JSONL fallback. "
            "Set it to https://retail-campaign-automation.onrender.com/audit to run."
        )


def test_live_forecast_api_get_all_stores_info():
    stores = forecast_tool.get_all_stores_info()
    assert isinstance(stores, dict)
    assert len(stores) > 0
    sample_id, sample = next(iter(stores.items()))
    assert isinstance(sample_id, int)
    assert isinstance(sample.get("last_day"), int)


def test_live_forecast_api_get_store_info_known_store():
    stores = forecast_tool.get_all_stores_info()
    store_id = next(iter(stores))
    info = forecast_tool.get_store_info(store_id)
    assert info is not None
    assert info["store_id"] == store_id
    assert isinstance(info["last_day"], int)


def test_live_forecast_api_get_store_info_unknown_store_returns_none():
    # Store ID chosen to be implausibly large for the real dataset (Dunnhumby
    # Complete Journey has ~300-400 distinct stores).
    assert forecast_tool.get_store_info(999_999_999) is None


def test_live_forecast_api_get_prediction_returns_finite_number():
    stores = forecast_tool.get_all_stores_info()
    store_id, meta = next(iter(stores.items()))
    prediction = forecast_tool.get_prediction(store_id, meta["last_day"])
    assert isinstance(prediction, float)
    assert prediction == prediction  # not NaN
    assert prediction >= 0.0


def test_live_forecast_api_evaluation_window_is_fast_and_correct_shape():
    """Confirms the concurrent window fetch works against the real API and,
    just as importantly, that it completes in seconds rather than minutes -
    this is the exact code path that used to serialize 14 sequential
    requests with retry/backoff on top."""
    stores = forecast_tool.get_all_stores_info()
    store_id, meta = next(iter(stores.items()))
    start_day = max(0, meta["last_day"] - 70)  # leave room for the +47..+60 window

    started = time.monotonic()
    mean_val = forecast_tool.get_evaluation_window_forecast(store_id, start_day)
    elapsed = time.monotonic() - started

    assert isinstance(mean_val, float)
    assert mean_val >= 0.0
    # Generous ceiling: even fully sequential with zero retries this would be
    # well under a minute; concurrent fetch should be a few seconds.
    assert elapsed < 60, f"window fetch took {elapsed:.1f}s - concurrency may not be working"


def test_live_campaign_audit_api_returns_valid_schema():
    _require_campaign_audit_api_configured()
    runs = campaign_tool.get_audit_log()
    assert isinstance(runs, list)
    if not runs:
        pytest.skip("Live campaign audit API returned zero runs - nothing to validate shape against")
    sample = runs[0]
    assert "campaign_label" in sample
    assert "timing_window" in sample
    assert "run_timestamp" in sample
    assert "store_ids" in sample
    assert isinstance(sample["store_ids"], list)
    assert "campaign_provenance_status" in sample
    assert sample["campaign_provenance_status"] in ("NORMALIZED", "MISSING_STABLE_CAMPAIGN_ID")


def test_live_campaign_audit_api_only_calls_audit_endpoint(monkeypatch):
    """Regression guard for the endpoint allowlist: confirms no other path
    ever gets requested even when the configured URL points at /audit."""
    _require_campaign_audit_api_configured()
    requested_urls = []
    real_get = requests.get

    def spy_get(url, *args, **kwargs):
        requested_urls.append(url)
        return real_get(url, *args, **kwargs)

    monkeypatch.setattr(requests, "get", spy_get)
    campaign_tool.get_audit_log()
    from urllib.parse import urlparse

    # The allowlist is about the endpoint PATH: query params (e.g. the
    # ?schema=contract representation selector) do not change which endpoint
    # is called, so they must not trip the regression guard.
    assert all(urlparse(url).path.rstrip("/").endswith("/audit") for url in requested_urls)
