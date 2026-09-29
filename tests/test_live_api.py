"""Smoke tests against the real, deployed Forecast API and Campaign Audit API.

Included in the default suite (design change: live connectivity is part of
the contract the suite verifies). Point FORECAST_API_URL at a local
instance to run fully offline, or deselect with `-m "not live_api"`.

The campaign-audit checks default to the deployed Project 2 endpoint (an
explicit ``CAMPAIGN_AUDIT_API_URL`` - a local Project 2, for instance - still
wins), and the controls check defaults to the *deployed* Project 1 service:
both exist so the adapters are proven against the real contracts, not only
against mocks.

These are intentionally light-touch: they confirm connectivity, response
shape, and basic contract compliance, not specific business values (which
depend on whatever data the live services happen to hold right now). Keep
it that way - asserting on specific numbers here would make this suite
flaky against a live, changing backend.
"""
import os
import time

import httpx
import pytest

from tools import campaign_tool, forecast_tool

pytestmark = pytest.mark.live_api

_LIVE_CONTROL_WINDOW_DAYS = 30
_LIVE_CONTROL_PRE_DAYS = 61
_LIVE_CONTROL_K = 3


@pytest.fixture()
def live_campaign_audit_api(monkeypatch):
    """Point the adapter at the deployed audit endpoint unless one is configured.

    These checks exist to exercise the read-only path production uses, so they
    default to the deployed service instead of skipping to the JSONL fallback;
    an operator who set ``CAMPAIGN_AUDIT_API_URL`` (for example to a local
    Project 2) keeps that value.
    """
    monkeypatch.setenv("CAMPAIGN_AUDIT_API_URL",
                       os.getenv("CAMPAIGN_AUDIT_API_URL") or campaign_tool.DEFAULT_AUDIT_API_URL)


def _live_control_window(meta):
    """Coverage-feasible recent window for the live controls endpoint."""
    last_day = max(1, meta.get("last_day", 1))
    post_end = min(last_day, 711)
    post_start = max(1, post_end - _LIVE_CONTROL_WINDOW_DAYS + 1)
    pre_end = post_start - 1
    pre_start = max(1, pre_end - _LIVE_CONTROL_PRE_DAYS + 1)
    return pre_start, pre_end, post_start, post_end


def _live_control_envelope():
    """Fetch a valid live DiD envelope from the densest candidate stores.

    Project 1 is the coverage authority: it refuses a causal comparison for a
    store below its own 80% coverage rule with HTTP 422, and coverage varies by
    store. So this walks the densest stores in order, skips a 422, and lets any
    other failure propagate - a bad envelope is the contract violation under
    test, whereas "this particular store lacks coverage" is not.
    """
    stores = forecast_tool.get_all_stores_info()
    ranked = sorted(
        stores.items(),
        key=lambda item: (
            item[1].get("days_with_data", 0) / max(1, item[1].get("last_day", 1) - item[1].get("first_day", 0)),
            item[1].get("last_day", 0),
        ),
        reverse=True,
    )[:10]
    refused = []
    for store_id, meta in ranked:
        windows = _live_control_window(meta)
        try:
            return forecast_tool.get_control_comparison(store_id, *windows, k=_LIVE_CONTROL_K)
        except httpx.HTTPStatusError as error:
            response = error.response
            if response is not None and response.status_code == 422:
                refused.append((store_id, windows))
                continue
            raise
    pytest.skip(f"live controls API had no coverage-eligible window among {len(refused)} candidates")


def test_live_forecast_api_get_all_stores_info():
    stores = forecast_tool.get_all_stores_info()
    assert isinstance(stores, dict)
    assert len(stores) > 0
    sample_id, sample = next(iter(stores.items()))
    assert isinstance(sample_id, int)
    assert isinstance(sample.get("last_day"), int)


def test_live_forecast_controls_returns_valid_did_envelope():
    """Prove causal scale-up evidence against the live controls endpoint.

    Unlike mocked envelope checks, this records which real control pool the
    Project 1 service matched. It deliberately avoids asserting particular
    business values: sign, magnitude, and the exact control set are live data
    and may drift without breaking the contract.
    """
    envelope = _live_control_envelope()

    assert isinstance(envelope.get("store_id"), int)
    assert set(envelope) >= {"store_id", "windows", "matched_controls", "causal", "methodology"}
    causal = envelope["causal"]
    assert isinstance(causal, dict) and "did_uplift_pct" in causal
    uplift = causal["did_uplift_pct"]
    assert uplift is None or (isinstance(uplift, (int, float)) and not isinstance(uplift, bool))
    controls = envelope["matched_controls"]
    assert isinstance(controls, list) and controls
    assert all(isinstance(control.get("store_id"), int) for control in controls)
    methodology = envelope["methodology"]
    assert isinstance(methodology, dict)
    assert set(methodology) >= {"matching", "effect", "caveat"}


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
    httpx with retry/backoff on top."""
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


def test_live_campaign_audit_api_returns_valid_schema(live_campaign_audit_api):
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


def test_live_campaign_audit_api_only_calls_audit_endpoint(monkeypatch, live_campaign_audit_api):
    """Regression guard for the endpoint allowlist: confirms no other path
    ever gets requested even when the configured URL points at /audit."""
    requested_urls = []
    real_get = httpx.get

    def spy_get(url, *args, **kwargs):
        requested_urls.append(url)
        return real_get(url, *args, **kwargs)

    monkeypatch.setattr(httpx, "get", spy_get)
    campaign_tool.get_audit_log()
    from urllib.parse import urlparse

    # The allowlist is about the endpoint PATH: query params (e.g. the
    # ?schema=contract representation selector) do not change which endpoint
    # is called, so they must not trip the regression guard.
    assert all(urlparse(url).path.rstrip("/").endswith("/audit") for url in requested_urls)
