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