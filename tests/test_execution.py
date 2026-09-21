"""Tests for the execution layer (execution/connector.py + journal).

The loop's ``execute`` stage: gated, idempotent, reversible, auditable.
Endpoint tests skip without fastapi (CI runs them)."""
from __future__ import annotations

import json

import pytest

from execution import journal
from execution.connector import (
    DryRunConnector,
    ExecutionAlreadyReversed,
    ExecutionNotFound,
    ExecutionRefused,
    execute_recommendation,
    execution_state,
    idempotency_key,
    reverse_execution,
)

APPROVED = {
    "store_id": 7,
    "recommendation_id": "rec-abc",
    "recommendation": "EXTEND_INTERVENTION",
    "approval_id": "approval-1",
    "approved": True,
    "decided_at": "2026-09-20T10:00:00+00:00",
}


@pytest.fixture(autouse=True)
def _isolated_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("EXECUTION_JOURNAL_PATH", str(tmp_path / "exec.jsonl"))


def test_refuses_unapproved_record():
    for patch in ({"approved": False}, {"approved": None}, {"decided_at": None}):
        with pytest.raises(ExecutionRefused):
            execute_recommendation({**APPROVED, **patch})
    # a bare recommendation record (never decided) is refused too
    with pytest.raises(ExecutionRefused):
        execute_recommendation({"store_id": 7, "recommendation_id": "rec-abc",
                                "recommendation": "EXTEND_INTERVENTION"})


def test_refuses_non_actionable_recommendation():
    with pytest.raises(ExecutionRefused):
        execute_recommendation({**APPROVED, "recommendation": "CONTINUE"})


def test_executes_and_journals():
    execution, created = execute_recommendation(APPROVED, actor="user:alice")
    assert created is True
    assert execution["connector"] == "dry-run"
    assert execution["connector_handle"].startswith("dry-run:")
    assert execution["reversed_at"] is None
    events = journal.read_events()
    assert len(events) == 1 and events[0]["event"] == "execution"


def test_idempotent_repeat_appends_nothing():
    first, created_first = execute_recommendation(APPROVED)
    second, created_second = execute_recommendation(APPROVED)
    assert created_first is True and created_second is False
    assert first["execution_id"] == second["execution_id"]
    assert len(journal.read_events()) == 1  # no second apply, no second line


def test_reversal_marks_state_and_is_terminal():
    execution, _ = execute_recommendation(APPROVED)
    reversed_execution = reverse_execution(execution["execution_id"], actor="user:bob")
    assert reversed_execution["reversed_at"] is not None
    assert reversed_execution["reversal_id"].startswith("rev-")
    assert len(journal.read_events()) == 2  # execution + reversal, appended
    with pytest.raises(ExecutionAlreadyReversed):
        reverse_execution(execution["execution_id"])


def test_reexecution_after_reversal_creates_a_new_execution():
    first, _ = execute_recommendation(APPROVED)
    reverse_execution(first["execution_id"])
    second, created = execute_recommendation(APPROVED)
    assert created is True
    assert second["execution_id"] != first["execution_id"]
    state = execution_state()
    assert len(state) == 2
    assert state[first["execution_id"]]["reversed_at"] is not None
    assert state[second["execution_id"]]["reversed_at"] is None


def test_reverse_unknown_execution_raises():
    with pytest.raises(ExecutionNotFound):
        reverse_execution("exec-does-not-exist")

def test_custom_connector_seam_receives_intent_and_reversal():
    calls = []

    class FakePosConnector:
        name = "fake-pos"

        def apply(self, intent):
            calls.append(("apply", dict(intent)))
            return "pos-handle-1"

        def reverse(self, handle):
            calls.append(("reverse", handle))

    execution, _ = execute_recommendation(APPROVED, connector=FakePosConnector(), actor="user:alice")
    assert execution["connector"] == "fake-pos"
    assert execution["connector_handle"] == "pos-handle-1"
    assert calls[0][0] == "apply" and calls[0][1]["recommendation"] == "EXTEND_INTERVENTION"
    reverse_execution(execution["execution_id"], connector=FakePosConnector())
    assert calls[1] == ("reverse", "pos-handle-1")


def test_dry_run_default_performs_no_external_io(monkeypatch):
    """The default connector must not touch the network: it is the safe default
    a real deployment opts out of by injecting an adapter."""
    import socket

    def explode(*args, **kwargs):
        raise AssertionError("dry-run connector attempted network I/O")
    monkeypatch.setattr(socket, "socket", explode)
    execution, created = execute_recommendation(APPROVED, connector=DryRunConnector())
    assert created is True and execution["connector"] == "dry-run"


def test_journal_is_append_only_and_skips_malformed_lines():
    execute_recommendation(APPROVED)
    path = journal.journal_path()
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
        handle.write(json.dumps({"event": "execution", "execution_id": "exec-manual"}) + "\n")
    events = journal.read_events()
    assert events[-1].get("execution_id") == "exec-manual"
    assert len(events) == 2  # malformed line ignored, never repaired or removed


def test_idempotency_key_is_stable_and_approval_specific():
    assert idempotency_key(APPROVED) == idempotency_key(dict(APPROVED))
    assert idempotency_key(APPROVED) != idempotency_key({**APPROVED, "recommendation_id": "rec-xyz"})


# --- endpoints (skip without fastapi) ------------------------------------------

def test_endpoint_execute_requires_auth_and_is_idempotent(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import app.main as app_main
    monkeypatch.setenv("EXECUTION_JOURNAL_PATH", str(tmp_path / "exec.jsonl"))
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("APPROVAL_AUTH_TOKEN", "tok")
    from memory.history import append_log
    append_log(dict(APPROVED))
    client = TestClient(app_main.app)
    assert client.post("/execute/7").status_code == 401
    assert client.post("/execute/7", headers={"Authorization": "Bearer bad"}).status_code == 403
    ok = client.post("/execute/7", headers={"Authorization": "Bearer tok"})
    assert ok.status_code == 200
    assert ok.json()["created"] is True
    assert ok.json()["execution"]["connector"] == "dry-run"
    again = client.post("/execute/7", headers={"Authorization": "Bearer tok"})
    assert again.json()["created"] is False  # idempotent
    listed = client.get("/executions", headers={"Authorization": "Bearer tok"})
    assert listed.json()["count"] == 1
    execution_id = ok.json()["execution"]["execution_id"]
    reversal = client.post(f"/executions/{execution_id}/reverse",
                           headers={"Authorization": "Bearer tok"})
    assert reversal.status_code == 200
    assert reversal.json()["execution"]["reversed_at"] is not None
    assert client.post(f"/executions/{execution_id}/reverse",
                       headers={"Authorization": "Bearer tok"}).status_code == 409
    assert client.post("/executions/exec-none/reverse",
                       headers={"Authorization": "Bearer tok"}).status_code == 404


def test_endpoint_execute_404_without_approval(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import app.main as app_main
    monkeypatch.setenv("EXECUTION_JOURNAL_PATH", str(tmp_path / "exec.jsonl"))
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("APPROVAL_AUTH_TOKEN", "tok")
    client = TestClient(app_main.app)
    assert client.post("/execute/999",
                       headers={"Authorization": "Bearer tok"}).status_code == 404


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
