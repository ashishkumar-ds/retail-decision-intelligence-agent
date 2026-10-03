"""The bundled demo recordings stay a faithful twin of the two external feeds.

A fresh clone cannot reach Project 2 or the deployed forecast service, yet
``scripts/seed_demo.sh`` promises a working demo. These cases pin the only
load-bearing facts behind that promise, each against the REAL runtime parser -
not a copy of its shape, so a recording that drifts from the contract fails
here instead of failing a reviewer with an empty board:

- the audit recording survives ``campaign_tool.get_audit_log()`` with the
  store universe intact, in first-seen order;
- the playback serves the real recorded numbers over HTTP: the
  ``forecast_tool`` strict envelope validators accept its answers, a
  same-day prediction byte-matches the bundle, the controls envelope carries
  the real per-store DiD uplift, and every demo store has signal.
"""
from __future__ import annotations

import http.server
import json
import pathlib
import threading

import pytest

import tools.campaign_tool as campaign_tool
import tools.forecast_tool as forecast_tool

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
AUDIT_RECORDING = DEMO / "campaign_audit.jsonl"
RECORDINGS = DEMO / "recordings.json"
STUB = DEMO / "forecast_stub.py"

EXPECTED_STORE_IDS = [31642, 317, 299, 289, 31582]


def _load_stub_module():
    """Import demo/forecast_stub.py by path: demo/ is recordings, not a package."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("demo_forecast_stub", STUB)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundle() -> dict:
    return json.loads(RECORDINGS.read_text(encoding="utf-8"))


def test_campaign_recording_loads_through_the_runtime_parser(monkeypatch, tmp_path):
    """The recording is a real audit line - the same code parses it."""
    target = tmp_path / "campaign_audit.jsonl"
    target.write_text(AUDIT_RECORDING.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("CAMPAIGN_AUDIT_LOG_PATH", str(target))
    monkeypatch.delenv("CAMPAIGN_AUDIT_API_URL", raising=False)

    runs = campaign_tool.get_audit_log()
    assert campaign_tool.get_store_ids_from_audit_log(runs) == EXPECTED_STORE_IDS
    first = campaign_tool.first_run_for_store(EXPECTED_STORE_IDS[0], runs)
    assert first is not None and first["campaign"] == "Campaign 18"


def test_playback_serves_the_recorded_numbers(monkeypatch):
    """The playback is indistinguishable from the service for the paths we use.

    The module runs in-process on a loopback socket; every adapter the serving
    path uses exercises it. The assertions compare against the bundle itself:
    a re-recorded bundle updates expectations by construction, while a broken
    server (wrong key, dropped field, invented number) fails. Real per-store
    values prove this is data, not a model: 317's recorded DiD is -26.77,
    not the +2.84 prior.
    """
    stub = _load_stub_module()
    bundle = _bundle()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), stub._Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("FORECAST_API_URL", f"http://127.0.0.1:{port}/")
    try:
        assert forecast_tool.warm_up(max_wait_seconds=5.0) is True
        stores = forecast_tool.get_all_stores_info(sleep_fn=lambda _seconds: None)
        for store_id in EXPECTED_STORE_IDS:  # every demo store has signal
            assert stores[store_id]["last_day"] == 711
        last_day = stores[EXPECTED_STORE_IDS[0]]["last_day"]
        prediction = forecast_tool.get_prediction(
            EXPECTED_STORE_IDS[0], last_day, sleep_fn=lambda _seconds: None)
        recorded = bundle["predictions"][f"{EXPECTED_STORE_IDS[0]}/{last_day}"]
        assert prediction == recorded  # byte-identical to the recorded answer
        controls = forecast_tool.get_control_comparison(
            317, 500, 545, 547, 560, sleep_fn=lambda _seconds: None)
        assert controls["causal"]["did_uplift_pct"] == pytest.approx(-26.77)
        assert controls["causal"]["did_uplift_pct"] == pytest.approx(
            bundle["controls"]["317"]["causal"]["did_uplift_pct"])
        window = forecast_tool.get_evaluation_window_forecast(
            31642, 711, window_start_offset=0, window_end_offset=2,
            sleep_fn=lambda _seconds: None)
        expected = sum(bundle["predictions"][f"31642/{day}"] for day in (711, 712, 713)) / 3
        assert window == pytest.approx(expected)
        actuals = forecast_tool.get_actuals(
            EXPECTED_STORE_IDS[0], 550, 552, sleep_fn=lambda _seconds: None)
        assert [obs["day"] for obs in actuals["observations"]] == [550, 551, 552]
        assert actuals["observations"] == pytest.approx(
            [obs for obs in bundle["actuals"][str(EXPECTED_STORE_IDS[0])]["observations"]
             if 550 <= obs["day"] <= 552])
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
    assert AUDIT_RECORDING.name in script
    assert STUB.name in script


def test_seed_demo_reports_external_services_truthfully(tmp_path):
    """The closing banner must describe the run that actually happened.

    Default everything: banner claims fully local. Point the inputs at one
    real service URL: banner names exactly that service and never claims
    "no external services". seed_demo.sh owns this claim in one place.
    """
    import shutil
    import subprocess

    script = ROOT / "scripts" / "seed_demo.sh"
    if shutil.which("bash") is None or shutil.which("curl") is None:
        pytest.skip("seed_demo.sh needs bash and curl")
    rendered = script.read_text(encoding="utf-8")
    anchor = "USE_LOCAL_AUDIT=1"
    closing = rendered[rendered.index(anchor):]
    fake_forecast = "https://forecast.example.invalid/"
    fake_audit = "https://audit.example.invalid/audit"
    runner = tmp_path / "banner.sh"
    runner.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        f'HAS_FORECAST_URL="{fake_forecast}"\n'
        f'HAS_AUDIT_URL="{fake_audit}"\n'
        "_is_remote() { case \"$1\" in *localhost*|*127.0.0.1*|\"\") return 1;; *) return 0;; esac; }\n"
        + closing + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(["bash", str(runner)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "served with external services" in result.stdout
    assert fake_forecast in result.stdout
    assert fake_audit in result.stdout
    assert "no external services" not in result.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))