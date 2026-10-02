"""The bundled demo fixtures stay a faithful stand-in for the two external feeds.

A fresh clone cannot reach Project 2 or the deployed forecast service, yet
``scripts/seed_demo.sh`` promises a working demo. These cases pin the only two
load-bearing facts behind that promise, each against the REAL runtime parser -
not a copy of its shape, so a fixture that drifts from the contract fails here
instead of failing a reviewer with an empty board:

- the audit fixture survives ``campaign_tool.get_audit_log()`` with the
  store universe intact, in first-seen order;
- the forecast stub satisfies the real ``forecast_tool`` validators over HTTP
  (``/health`` warm, ``/stores`` metadata, ``/predict`` numbers, and a
  well-formed ``/controls`` causal envelope), and covers every demo store.
"""
from __future__ import annotations

import http.server
import pathlib
import threading

import pytest

import tools.campaign_tool as campaign_tool
import tools.forecast_tool as forecast_tool

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
AUDIT_FIXTURE = DEMO / "campaign_audit.jsonl"
STUB = DEMO / "forecast_stub.py"

EXPECTED_STORE_IDS = [31642, 317, 299, 289, 31582]


def _load_stub_module():
    """Import demo/forecast_stub.py by path: demo/ is fixture data, not a package."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("demo_forecast_stub", STUB)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_campaign_fixture_loads_through_the_runtime_parser(monkeypatch, tmp_path):
    """The fixture is what a real audit file looks like - the same code parses it."""
    target = tmp_path / "campaign_audit.jsonl"
    target.write_text(AUDIT_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("CAMPAIGN_AUDIT_LOG_PATH", str(target))
    monkeypatch.delenv("CAMPAIGN_AUDIT_API_URL", raising=False)

    runs = campaign_tool.get_audit_log()
    assert campaign_tool.get_store_ids_from_audit_log(runs) == EXPECTED_STORE_IDS
    first = campaign_tool.first_run_for_store(EXPECTED_STORE_IDS[0], runs)
    assert first is not None and first["campaign"] == "Campaign 18"


def test_forecast_stub_serves_the_real_contract(monkeypatch):
    """forecast_tool cannot tell the stub from the deployed service.

    The stub runs in-process on a loopback socket; every adapter the serving
    path uses exercises it, and the strict envelope validators accept its
    answers - spare fields are fine, missing ones are not.
    """
    stub = _load_stub_module()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), stub._Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("FORECAST_API_URL", f"http://127.0.0.1:{port}/")
    try:
        assert forecast_tool.warm_up(max_wait_seconds=5.0) is True
        stores = forecast_tool.get_all_stores_info(sleep_fn=lambda _seconds: None)
        for store_id in EXPECTED_STORE_IDS:  # every demo store has signal
            assert stores[store_id]["last_day"] == 560
        last_day = stores[EXPECTED_STORE_IDS[0]]["last_day"]
        prediction = forecast_tool.get_prediction(
            EXPECTED_STORE_IDS[0], last_day + 20, sleep_fn=lambda _seconds: None)
        assert prediction > 0
        controls = forecast_tool.get_control_comparison(
            EXPECTED_STORE_IDS[0], 500, 545, 547, 560, sleep_fn=lambda _seconds: None)
        assert controls["causal"]["did_uplift_pct"] == pytest.approx(2.84)
        actuals = forecast_tool.get_actuals(
            EXPECTED_STORE_IDS[0], 550, 552, sleep_fn=lambda _seconds: None)
        assert [obs["day"] for obs in actuals["observations"]] == [550, 551, 552]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_seed_demo_never_inherits_an_absent_configuration():
    """seed_demo.sh must use :- fallbacks, so a real deployment is never overwritten.

    The repo's own scar: unset vars silently kept code defaults (SWEEP_ENABLED).
    This script sets two vars deliberately - but only when the operator has not.
    """
    script = (ROOT / "scripts" / "seed_demo.sh").read_text(encoding="utf-8")
    assert 'CAMPAIGN_AUDIT_LOG_PATH:-' in script
    assert 'FORECAST_API_URL:-' in script
    assert AUDIT_FIXTURE.name in script
    assert STUB.name in script


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))