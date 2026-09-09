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