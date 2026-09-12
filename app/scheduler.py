"""Background sweep scheduler — the autonomous heartbeat of the recovery agent.

Runs the same sweep as ``POST /monitor/sweep`` on a fixed interval so the
attention queue answers "which stores need priority" every morning without
anyone remembering to call an endpoint. Opt-in via ``SWEEP_ENABLED=1`` so
test runs and offline work never trigger live-API calls unintentionally.

Design constraints (fail-safe, per project philosophy):
- A failed sweep is logged and retried on the next tick; it never kills the
  loop and never mutates state partially (persistence stays gated by the
  existing verification gate inside the sweep itself).
- stdlib threading only: no new dependencies; daemon thread stops cleanly
  on shutdown via an Event.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable

from approvals.ledger import utcnow_iso as _utcnow_iso

logger = logging.getLogger("retail_decision_agent.scheduler")

DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60  # daily heartbeat
SWEEP_ENABLED_ENV = "SWEEP_ENABLED"
SWEEP_INTERVAL_ENV = "SWEEP_INTERVAL_SECONDS"


def sweep_enabled() -> bool:
    """Opt-in: the scheduler only runs when explicitly enabled via env."""
    return os.getenv(SWEEP_ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def sweep_interval_seconds() -> int:
    raw = os.getenv(SWEEP_INTERVAL_ENV, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS
    return value if value > 0 else DEFAULT_INTERVAL_SECONDS




class SweepScheduler:
    """Fixed-interval background scheduler with observable, fail-safe ticks."""

    def __init__(self, sweep_fn: Callable[[], dict], interval_seconds: int):
        self._sweep_fn = sweep_fn
        self._interval = max(1, int(interval_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._status: dict = {
            "enabled": False,
            "running": False,
            "interval_seconds": self._interval,
            "sweeps_completed": 0,
            "sweeps_failed": 0,
            "last_sweep_at": None,
            "last_error": None,
            "next_sweep_at": None,
        }

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._status["enabled"] = True
            self._status["running"] = True
            self._status["next_sweep_at"] = _utcnow_iso()
            self._thread = threading.Thread(
                target=self._run_loop, name="sweep-scheduler", daemon=True,
            )
            self._thread.start()
            logger.info("Sweep scheduler started (interval %ss)", self._interval)

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            self._status["running"] = False
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._tick()
            self._stop_event.wait(self._interval)
            with self._lock:
                self._status["next_sweep_at"] = _utcnow_iso()

    def _tick(self) -> None:
        try:
            self._sweep_fn()
        except Exception as error:  # fail-safe: never let one bad sweep kill the loop
            with self._lock:
                self._status["sweeps_failed"] += 1
                self._status["last_error"] = f"{type(error).__name__}: {error}"
                self._status["last_sweep_at"] = _utcnow_iso()
            logger.error("[SWEEP SCHEDULER] sweep failed: %s: %s", type(error).__name__, error)
            return
        with self._lock:
            self._status["sweeps_completed"] += 1
            self._status["last_sweep_at"] = _utcnow_iso()
            self._status["next_sweep_at"] = _utcnow_iso()