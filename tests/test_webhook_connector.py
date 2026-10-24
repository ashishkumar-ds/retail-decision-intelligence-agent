"""Tests for the example webhook adapter (execution/webhook_connector.py).

Proves a REAL external write flows through the loop's gate, idempotency,
reversal and audit — offline, by capturing the POSTs instead of sending them.
"""
from __future__ import annotations

import json

import pytest

from execution import journal
from execution.connector import execute_recommendation, reverse_execution
from execution.webhook_connector import WebhookConnector

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


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture()
def _captured_posts(monkeypatch):
    posts: list[dict] = []

    def _fake_urlopen(request, timeout=None):
        posts.append(json.loads(request.data.decode("utf-8")))
        if posts[-1].get("type") == "apply":
            return _FakeResponse({"id": "remote-7"})
        return _FakeResponse({"ok": True})

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    return posts


def test_webhook_apply_posts_intent_and_journals_remote_handle(_captured_posts):
    connector = WebhookConnector(url="https://pos.example/hooks/apply", token="t")
    execution, created = execute_recommendation(APPROVED, connector=connector)
    assert created is True
    assert execution["connector"] == "webhook"
    assert execution["connector_handle"] == "webhook:remote-7"
    assert _captured_posts[0]["type"] == "apply"
    assert _captured_posts[0]["store_id"] == 7
    # intent (before the POST) + completed execution (after it)
    assert [e["event"] for e in journal.read_events()] == ["execution_status", "execution"]


def test_webhook_reverse_posts_cancellation(_captured_posts):
    connector = WebhookConnector(url="https://pos.example/hooks/apply")
    execution, _ = execute_recommendation(APPROVED, connector=connector)
    updated = reverse_execution(execution["execution_id"], connector=connector)
    assert updated["reversed_at"] is not None
    assert _captured_posts[-1] == {"type": "reverse", "handle": "webhook:remote-7"}
    events = journal.read_events()
    assert [e["event"] for e in events] == [
        "execution_status", "execution", "execution_status", "reversal"]


def test_webhook_idempotent_on_retry(_captured_posts):
    connector = WebhookConnector(url="https://pos.example/hooks/apply")
    first, created = execute_recommendation(APPROVED, connector=connector)
    second, created_again = execute_recommendation(APPROVED, connector=connector)
    assert (created, created_again) == (True, False)
    assert second["execution_id"] == first["execution_id"]
    assert sum(1 for p in _captured_posts if p.get("type") == "apply") == 1


def test_webhook_failure_journals_nothing(monkeypatch):
    import urllib.error

    def _boom(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    connector = WebhookConnector(url="https://pos.example/hooks/apply")
    with pytest.raises(Exception):
        execute_recommendation(APPROVED, connector=connector)
    # The failed attempt is not silent: the "applying" intent is a trace in the
    # journal, but no completed execution state exists (the fold ignores it).
    events = journal.read_events()
    assert [e["event"] for e in events] == ["execution_status"]
    assert events[0]["status"] == "applying" and events[0]["connector"] == "webhook"
    from execution.connector import execution_state as _state
    assert _state() == {}


# --- operator selection ------------------------------------------------------

def test_resolve_connector_defaults_to_dry_run(monkeypatch):
    from execution.connector import DryRunConnector, resolve_connector

    monkeypatch.delenv("EXECUTION_CONNECTOR", raising=False)
    assert isinstance(resolve_connector(), DryRunConnector)


def test_resolve_connector_webhook_needs_url(monkeypatch):
    from execution.connector import (
        ExecutionMisconfigured,
        resolve_connector,
    )
    from execution.webhook_connector import WebhookConnector

    monkeypatch.setenv("EXECUTION_CONNECTOR", "webhook")
    monkeypatch.delenv("EXECUTION_WEBHOOK_URL", raising=False)
    with pytest.raises(ExecutionMisconfigured):
        resolve_connector()  # asked for real writes: 503, never silent dry-run
    monkeypatch.setenv("EXECUTION_WEBHOOK_URL", "https://pos.example/hooks")
    connector = resolve_connector()
    assert isinstance(connector, WebhookConnector)
    assert connector.url == "https://pos.example/hooks"
    monkeypatch.setenv("EXECUTION_CONNECTOR", "bogus")
    with pytest.raises(ExecutionMisconfigured):
        resolve_connector()


def test_execute_endpoint_uses_configured_connector(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import app.main as app_main

    monkeypatch.setenv("EXECUTION_CONNECTOR", "webhook")
    monkeypatch.setenv("EXECUTION_WEBHOOK_URL", "https://pos.example/hooks")
    monkeypatch.setenv("EXECUTION_JOURNAL_PATH", str(tmp_path / "exec.jsonl"))
    monkeypatch.setenv("APPROVAL_AUTH_TOKEN", "tok")
    posts: list[dict] = []

    import json as _json

    class _FakeResponse:
        def read(self):
            return _json.dumps({"id": "remote-9"}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _fake_urlopen(request, timeout=None):
        posts.append(_json.loads(request.data.decode("utf-8")))
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    monkeypatch.setattr(app_main, "_approved_record_for", lambda store_id: dict(APPROVED))
    client = TestClient(app_main.app)
    response = client.post("/execute/7", headers={"Authorization": "Bearer tok"}, json={})
    assert response.status_code == 200
    assert response.json()["execution"]["connector_handle"] == "webhook:remote-9"
    assert posts[0]["type"] == "apply"


def test_execute_endpoint_misconfigured_connector_is_503(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import app.main as app_main

    monkeypatch.setenv("EXECUTION_CONNECTOR", "webhook")
    monkeypatch.delenv("EXECUTION_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("EXECUTION_JOURNAL_PATH", str(tmp_path / "exec.jsonl"))
    monkeypatch.setenv("APPROVAL_AUTH_TOKEN", "tok")
    monkeypatch.setattr(app_main, "_approved_record_for", lambda store_id: dict(APPROVED))
    client = TestClient(app_main.app)
    response = client.post("/execute/7", headers={"Authorization": "Bearer tok"}, json={})
    assert response.status_code == 503
