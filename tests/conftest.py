"""Shared test fixtures.

The pending-approval store is a module-global SQLite-backed store whose path is
read from ``PENDING_APPROVAL_STATE_PATH`` on every operation. Without isolation
a shared state file would leak pending approvals from one test to the next; this
autouse fixture points every test at a fresh, per-test database file. Tests that
exercise the store explicitly can also pin a path via ``PENDING_APPROVAL_STATE_PATH``.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_pending_approval_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "pending_approvals.db"))


@pytest.fixture(autouse=True)
def _isolate_offpath_llm_telemetry(tmp_path, monkeypatch):
    """Point off-path LLM telemetry at a per-test file.

    The LLM layers record guard vetoes and silent degradations to an append-only
    trail (``rag/llm_telemetry.py``). Synthetic drafts from the test suite must
    never enter the real trail - same reasoning as the pending-approval store
    above. Tests that exercise telemetry read this path via
    ``OFFPATH_LLM_LOG_PATH``.
    """
    monkeypatch.setenv("OFFPATH_LLM_LOG_PATH", str(tmp_path / "offpath_llm.jsonl"))


@pytest.fixture(autouse=True)
def _disable_board_self_refresh(monkeypatch):
    """The live board re-evaluates against the forecast API on a stale read.

    The suite must never fan out over a live upstream (same rule as the warm-up
    fixture below); each test opts in by patching ``_board_self_refresh_enabled``
    or the refresh seam it needs.
    """
    monkeypatch.setenv("BOARD_SELF_REFRESH", "0")


@pytest.fixture(autouse=True)
def _isolate_upstream_warmup(monkeypatch):
    """Keep the warm-up daemon inside the test that started it.

    ``_kick_upstream_warmup`` starts a daemon thread that pings the LIVE
    forecast/campaign upstreams and then backfills the module-global
    ``_observed_baselines`` from whatever ``RECOMMENDATION_LOG_PATH`` points at
    when it finally gets there. A real ping takes seconds, so the thread
    routinely outlived its test and landed a baseline in the next one -
    ``test_refresh_observed_baselines_fails_open`` asserted the dict was empty
    while a store-9 write from the previous test was still in flight.

    The suite must never touch a live upstream, so the pings are stubbed for
    every test (the throttle test re-patches them to count calls), and any
    in-flight warm-up thread is joined before the next test starts.
    """
    import threading

    from app import main

    monkeypatch.setattr(main, "ping_forecast_upstream", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "ping_campaign_upstream", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_last_warmup_kick", 0.0)
    main._observed_baselines.clear()
    yield
    for thread in threading.enumerate():
        if thread.name == "upstream-warmup":
            thread.join(timeout=10)
    main._observed_baselines.clear()