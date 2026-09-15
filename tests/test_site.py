"""Tests for the operator site (server-rendered UI).

Contract: the UI is a view over the same JSON API data and reuses the same
fail-closed token gate. Read-only pages render without auth; mutating forms
carry the bearer token and are validated by the same 503/403 contract.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="Site tests require FastAPI/Pydantic.")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "recommendation.jsonl"))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "pending.db"))
    monkeypatch.setenv(main.APPROVAL_AUTH_TOKEN_ENV, "test-token")
    main._pending_approvals.clear()
    with TestClient(main.app) as c:
        yield c


def _seed_pending(client, monkeypatch):
    """Create a real, gate-passing pending approval by running the pipeline
    with mocked forecast/audit tools (same seams as test_api_endpoints)."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(main, "get_audit_log", lambda: [{"run_timestamp": now, "store_ids": [1]}])
    monkeypatch.setattr(main, "get_store_info", lambda store_id: {"last_day": 1})
    monkeypatch.setattr(main, "get_prediction", lambda store_id, day: 100)
    headers = {"Authorization": "Bearer test-token"}
    run = client.post("/recommendations/run", headers=headers)
    assert run.status_code == 200, run.text
    return 1  # the mocked pipeline targets store 1


def test_all_read_only_pages_render_without_token(client):
    for path in ("/ui", "/ui/approvals", "/ui/simulate", "/ui/evals", "/ui/why/317"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "dunnhumby" in response.text  # theme footer present


def test_dashboard_shows_attention_queue_and_kpis(client, monkeypatch):
    store_id = _seed_pending(client, monkeypatch)
    response = client.get("/ui")
    assert response.status_code == 200
    assert str(store_id) in response.text and "pending approvals" in response.text


def test_approvals_page_lists_pending_with_decision_forms(client, monkeypatch):
    store_id = _seed_pending(client, monkeypatch)
    response = client.get("/ui/approvals")
    assert response.status_code == 200
    assert f"/ui/approve/{store_id}" in response.text and f"/ui/reject/{store_id}" in response.text


def test_ui_approve_with_valid_token_records_decision(client, monkeypatch):
    store_id = _seed_pending(client, monkeypatch)
    response = client.post(f"/ui/approve/{store_id}",
                           data={"token": "test-token", "actor": "operator-site"})
    assert response.status_code == 200
    assert "approved" in response.text.lower()
    assert store_id not in main._pending_approvals  # queue drained
    decided = main.read_log()
    assert any(e["store_id"] == store_id and e.get("approved") for e in decided)


def test_ui_approve_with_wrong_token_fails_closed(client, monkeypatch):
    store_id = _seed_pending(client, monkeypatch)
    response = client.post(f"/ui/approve/{store_id}", data={"token": "wrong-token", "actor": "x"})
    assert response.status_code == 200  # page renders, banner carries the error
    assert "403" in response.text
    assert store_id in main._pending_approvals  # nothing decided


def test_ui_mutations_disabled_without_server_token(client, monkeypatch):
    store_id = _seed_pending(client, monkeypatch)
    monkeypatch.delenv(main.APPROVAL_AUTH_TOKEN_ENV, raising=False)
    response = client.post(f"/ui/approve/{store_id}", data={"token": "whatever", "actor": "x"})
    assert "503" in response.text
    assert store_id in main._pending_approvals


def test_ui_reject_records_rejection(client, monkeypatch):
    store_id = _seed_pending(client, monkeypatch)
    response = client.post(f"/ui/reject/{store_id}",
                           data={"token": "test-token", "actor": "operator-site"})
    assert response.status_code == 200
    assert store_id not in main._pending_approvals
    decided = main.read_log()
    assert any(e["store_id"] == store_id and e.get("approved") is False for e in decided)


def test_simulate_page_renders_result(client, monkeypatch):
    def fake_actuals(store_id, start_day, end_day, **kwargs):
        return {"store_id": store_id,
                "observations": [{"day": d, "sales_value": 100.0} for d in range(start_day, end_day + 1)]}

    monkeypatch.setattr(main, "get_actuals", fake_actuals)
    response = client.get("/ui/simulate", params={"store_id": 317, "started_day": 650})
    assert response.status_code == 200
    assert "REVIEW_ZONE" in response.text and "incremental value" in response.text


def test_simulate_page_renders_error_banner_when_service_down(client, monkeypatch):
    import httpx

    def down(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(main, "get_actuals", down)
    response = client.get("/ui/simulate", params={"store_id": 317, "started_day": 650})
    assert response.status_code == 200
    assert "Simulation unavailable" in response.text


def test_evals_page_renders_with_and_without_history(tmp_path, monkeypatch, client):
    monkeypatch.chdir(tmp_path)  # logs/eval_runs.jsonl does not exist here
    response = client.get("/ui/evals")
    assert response.status_code == 200
    assert "No eval runs recorded" in response.text

    import evaluation.run_evals as re_  # noqa: N813
    monkeypatch.setattr(re_, "EVAL_LOG_PATH", tmp_path / "eval_runs.jsonl")
    from evaluation import run_evals as runner
    monkeypatch.setattr(runner, "EVAL_LOG_PATH", tmp_path / "eval_runs.jsonl")
    (tmp_path / "eval_runs.jsonl").write_text(
        '{"run_at": "2026-09-12T00:00:00+00:00", "total": 22, "passed": 22, "failed": 0, '
        '"failed_case_ids": []}\n', encoding="utf-8"
    )
    response = client.get("/ui/evals")
    assert response.status_code == 200 and "22" in response.text
