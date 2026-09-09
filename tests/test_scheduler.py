"""Scheduler and heartbeat tests — all offline (no live API, no real sweeps).

Also locks in the restart-safety guarantee of the pending-approval queue:
the in-memory view is rebuilt from the fsync'd append-only log at startup,
so a restart never loses pending approvals.
"""
from unittest import mock

from fastapi.testclient import TestClient

import app.main as main
import tools.forecast_tool as forecast_tool
from app.scheduler import (
    DEFAULT_INTERVAL_SECONDS,
    SweepScheduler,
    sweep_enabled,
    sweep_interval_seconds,
)

# --- env helpers -------------------------------------------------------------


def test_scheduler_disabled_by_default(monkeypatch):
    for var in ("SWEEP_ENABLED",):
        monkeypatch.delenv(var, raising=False)
    assert sweep_enabled() is False


def test_scheduler_enabled_via_env(monkeypatch):
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("SWEEP_ENABLED", value)
        assert sweep_enabled() is True
    monkeypatch.setenv("SWEEP_ENABLED", "0")
    assert sweep_enabled() is False


def test_interval_defaults_and_invalid_values(monkeypatch):
    monkeypatch.delenv("SWEEP_INTERVAL_SECONDS", raising=False)
    assert sweep_interval_seconds() == DEFAULT_INTERVAL_SECONDS
    monkeypatch.setenv("SWEEP_INTERVAL_SECONDS", "not-a-number")
    assert sweep_interval_seconds() == DEFAULT_INTERVAL_SECONDS
    monkeypatch.setenv("SWEEP_INTERVAL_SECONDS", "-5")
    assert sweep_interval_seconds() == DEFAULT_INTERVAL_SECONDS
    monkeypatch.setenv("SWEEP_INTERVAL_SECONDS", "3600")
    assert sweep_interval_seconds() == 3600


# --- scheduler behaviour ------------------------------------------------------


def _make_scheduler(ticks: list) -> SweepScheduler:
    calls = []

    def sweep():
        calls.append(1)
        if ticks:
            raise ticks.pop(0)

    return SweepScheduler(sweep, interval_seconds=1), calls


def test_scheduler_runs_and_survives_failures():
    scheduler, calls = _make_scheduler([RuntimeError("boom")])
    scheduler.start()
    try:
        # Tick 1 raises, tick 2 succeeds: the loop must survive the failure.
        import time
        for _ in range(200):
            if scheduler.status()["sweeps_completed"] >= 1:
                break
            time.sleep(0.01)
    finally:
        scheduler.stop()
    status = scheduler.status()
    assert status["running"] is False
    assert status["sweeps_completed"] >= 1
    assert status["sweeps_failed"] >= 1
    assert "RuntimeError" in status["last_error"]
    assert status["last_sweep_at"] is not None
    assert len(calls) >= 2


def test_scheduler_stop_is_idempotent():
    scheduler, _ = _make_scheduler([])
    scheduler.start()
    scheduler.stop()
    scheduler.stop()  # second stop must not raise
    assert scheduler.status()["running"] is False


def test_scheduler_start_is_idempotent():
    scheduler, _ = _make_scheduler([])
    scheduler.start()
    first_thread = scheduler._thread
    scheduler.start()  # second start must not spawn a second loop
    assert scheduler._thread is first_thread
    scheduler.stop()


# --- app wiring ---------------------------------------------------------------


def test_health_reports_scheduler_status():
    client = TestClient(main.app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    scheduler_status = body["scheduler"]
    assert scheduler_status["enabled"] is False  # tests never auto-enable sweeps
    assert scheduler_status["interval_seconds"] >= 1
    assert scheduler_status["sweeps_completed"] == 0


def test_app_startup_does_not_start_scheduler_by_default():
    # Without SWEEP_ENABLED the lifecycle hooks must leave the loop off.
    assert main._sweep_scheduler.status()["running"] is False


def test_warm_up_returns_true_when_service_answers():
    """A service that answers 200 on the first poll needs no waiting."""
    fake = mock.Mock(status_code=200)
    with mock.patch.object(forecast_tool.requests, "get", return_value=fake) as get, \
         mock.patch.object(forecast_tool.time, "sleep") as sleep:
        assert forecast_tool.warm_up(max_wait_seconds=30) is True
    assert get.call_count == 1
    sleep.assert_not_called()


def test_warm_up_polls_through_cold_start_until_healthy():
    """Simulate a Render cold start: 503, 503, then 200 — the warm-up must
    keep polling (with sleeps) and eventually succeed."""
    responses = [mock.Mock(status_code=503), mock.Mock(status_code=503), mock.Mock(status_code=200)]
    with mock.patch.object(forecast_tool.requests, "get", side_effect=responses) as get, \
         mock.patch.object(forecast_tool.time, "sleep") as sleep:
        assert forecast_tool.warm_up(max_wait_seconds=120) is True
    assert get.call_count == 3
    assert sleep.call_count == 2


def test_warm_up_gives_up_after_budget():
    """A service that never answers must return False, not hang forever."""
    fake = mock.Mock(status_code=503)
    with mock.patch.object(forecast_tool.requests, "get", return_value=fake), \
         mock.patch.object(forecast_tool.time, "sleep") as sleep, \
         mock.patch.object(forecast_tool.time, "monotonic",
                           side_effect=[0, 10, 20, 30, 40, 50, 60, 200]):
        assert forecast_tool.warm_up(max_wait_seconds=50) is False
    assert sleep.called


def test_warm_up_treats_connection_errors_as_retryable():
    with mock.patch.object(forecast_tool.requests, "get",
                           side_effect=[forecast_tool.requests.ConnectionError("down"),
                                        mock.Mock(status_code=200)]), \
         mock.patch.object(forecast_tool.time, "sleep"):
        assert forecast_tool.warm_up(max_wait_seconds=120) is True


# --- restart-safe approvals ----------------------------------------------------


def test_pending_approvals_survive_log_replay():
    """The durability contract: rebuilding from the log restores pending
    approvals and drops decided stores."""
    log_records = [
        {"store_id": 1, "recommendation_id": "r1", "requires_human_approval": True},
        {"store_id": 2, "recommendation_id": "r2", "requires_human_approval": True},
        {"store_id": 3, "recommendation_id": "r3", "requires_human_approval": False},
        {"store_id": 2, "recommendation_id": "r2", "requires_human_approval": True,
         "approved": True, "decided_at": "2026-08-30T00:00:00+00:00"},
        {"store_id": 1, "recommendation_id": "r1-bad", "not_a_dict_marker": None},
    ]
    with mock.patch.object(main, "read_log", return_value=log_records):
        pending = main._rebuild_pending_approvals()
    assert set(pending.keys()) == {1}
    assert pending[1]["recommendation_id"] == "r1"
    with mock.patch.object(main, "read_log", return_value=[]):
        assert main._rebuild_pending_approvals() == {}