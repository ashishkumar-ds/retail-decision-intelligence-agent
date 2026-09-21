"""Tests for approver identity/roles (approvals/identity.py) and the
provenance + /metrics wiring (endpoint tests skip without fastapi - the
Termux dev box cannot build it; CI runs them)."""
from __future__ import annotations

import pytest

from approvals.identity import (
    IdentityNotConfigured,
    Principal,
    parse_token_map,
    require_decision_role,
    resolve_principal,
)

# --- token map parsing ---------------------------------------------------------

def test_parse_token_map_valid():
    assert parse_token_map("tok1:alice:approver, tok2:bob:viewer") == {
        "tok1": ("alice", "approver"),
        "tok2": ("bob", "viewer"),
    }


def test_parse_token_map_rejects_malformed_entry():
    with pytest.raises(ValueError):
        parse_token_map("tok1:alice:approver,bad-entry")
    with pytest.raises(ValueError):
        parse_token_map("tok1:alice")


def test_parse_token_map_rejects_unknown_role():
    with pytest.raises(ValueError):
        parse_token_map("tok1:alice:admin")


# --- resolution ----------------------------------------------------------------

def test_shared_token_legacy_mode(monkeypatch):
    monkeypatch.delenv("APPROVAL_TOKENS", raising=False)
    monkeypatch.setenv("APPROVAL_AUTH_TOKEN", "legacy-token")
    principal = resolve_principal("legacy-token")
    assert principal.role == "approver" and principal.user is None
    assert principal.label() == "shared-token"
    with pytest.raises(PermissionError):
        resolve_principal("wrong")


def test_no_credentials_raises_not_configured(monkeypatch):
    monkeypatch.delenv("APPROVAL_TOKENS", raising=False)
    monkeypatch.delenv("APPROVAL_AUTH_TOKEN", raising=False)
    with pytest.raises(IdentityNotConfigured):
        resolve_principal("anything")


def test_token_map_mode_resolves_named_principals(monkeypatch):
    monkeypatch.setenv("APPROVAL_TOKENS", "tok1:alice:approver,tok2:bob:viewer")
    monkeypatch.setenv("APPROVAL_AUTH_TOKEN", "legacy-token")
    alice = resolve_principal("tok1")
    assert alice == Principal(user="alice", role="approver", source="token-map")
    assert alice.label() == "user:alice"
    bob = resolve_principal("tok2")
    assert bob.role == "viewer"
    with pytest.raises(PermissionError):
        resolve_principal("legacy-token")  # shared token not valid in map mode


# --- role enforcement ----------------------------------------------------------

def test_viewer_cannot_decide():
    principal = Principal(user="bob", role="viewer", source="token-map")
    with pytest.raises(PermissionError):
        require_decision_role(principal)


def test_approver_can_decide():
    principal = Principal(user="alice", role="approver", source="token-map")
    assert require_decision_role(principal) is principal


# --- ledger provenance ---------------------------------------------------------

def test_ledger_records_decided_by(tmp_path, monkeypatch):
    monkeypatch.setenv("APPROVAL_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    from approvals.ledger import append_decision, read_decisions
    entry = append_decision(
        {"store_id": 1, "recommendation_id": "rec-1", "recommendation": "MONITOR"},
        "approve", "self-claimed-email",
        {"allowed": True, "checks": {}}, decided_by="user:alice")
    assert entry["decided_by"] == "user:alice"
    assert entry["actor"] == "self-claimed-email"  # claim preserved alongside proof
    stored = read_decisions()[0]
    assert stored["decided_by"] == "user:alice"


# --- endpoint wiring (skips without fastapi; runs in CI and via /usr/bin/python3) ----

def _seed_engine_and_client(monkeypatch, tmp_path, tokens_env):
    """Seed a REAL engine recommendation through /recommendations/run (the same
    pattern test_phase1 uses), so the record passes the decision-time gate."""
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient

    import app.main as app_main
    monkeypatch.delenv("APPROVAL_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("APPROVAL_TOKENS", tokens_env)
    monkeypatch.setenv("APPROVAL_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "state.db"))
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(app_main, "get_audit_log",
                        lambda: [{"run_timestamp": now, "store_ids": [1]}])
    monkeypatch.setattr(app_main, "get_store_info", lambda store_id: {"last_day": 1})
    monkeypatch.setattr(app_main, "get_prediction", lambda store_id, day: 100)
    client = TestClient(app_main.app)
    run = client.post("/recommendations/run",
                      headers={"Authorization": "Bearer tok-approve"})
    assert run.status_code == 200
    return client


def test_endpoint_approve_records_provenance_and_rejects_viewer(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    client = _seed_engine_and_client(
        monkeypatch, tmp_path, "tok-approve:alice:approver,tok-view:bob:viewer")
    viewer = client.post("/approve/1",
                         headers={"Authorization": "Bearer tok-view"}, json={})
    assert viewer.status_code == 403
    ok = client.post("/approve/1",
                     headers={"Authorization": "Bearer tok-approve"},
                     json={"actor": "alice@example.com"})
    assert ok.status_code == 200
    rec = ok.json()["recommendation"]
    assert rec["decided_by"] == "user:alice"      # authenticated principal
    assert rec["actor"] == "alice@example.com"    # caller claim kept alongside
    from approvals.ledger import read_decisions
    assert read_decisions()[-1]["decided_by"] == "user:alice"


def test_endpoint_metrics_counts(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import app.main as app_main
    monkeypatch.setenv("APPROVAL_AUTH_TOKEN", "tok")
    monkeypatch.setenv("APPROVAL_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "state.db"))
    client = TestClient(app_main.app)
    assert client.get("/metrics").status_code == 401  # credential set, no header
    monkeypatch.delenv("APPROVAL_AUTH_TOKEN", raising=False)
    assert client.get("/metrics").status_code == 503  # no credential -> fail closed
    monkeypatch.setenv("APPROVAL_AUTH_TOKEN", "tok")
    ok = client.get("/metrics", headers={"Authorization": "Bearer tok"})
    assert ok.status_code == 200
    assert 'retail_decisions_total{decision="approve"} 0' in ok.text
    assert "retail_pending_approvals 0" in ok.text
    # sweep heartbeat metrics (alert rules depend on these names)
    assert 'retail_sweeps_total{result="completed"}' in ok.text
    assert 'retail_sweeps_total{result="failed"}' in ok.text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
