"""Feature-switch (enable_*) and pydantic contract tests.

Covers the commerce-agents enable_* pattern: a disabled capability refuses
to serve (503) with the switch named in the detail, changes no other
behavior, and /health reports the current flag state. Also covers the
pydantic HTTP-contract gates (outcome request body, audit run schema).
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as app_main
from app.config import actuals_feedback_enabled, phase2_enabled, rag_enabled

ALL_FLAG_ENVS = ("RAG_ENABLED", "PHASE2_ENABLED", "ACTUALS_FEEDBACK_ENABLED")


def test_flags_default_enabled(monkeypatch):
    for name in ALL_FLAG_ENVS:
        monkeypatch.delenv(name, raising=False)
    assert rag_enabled() and phase2_enabled() and actuals_feedback_enabled()


@pytest.mark.parametrize("off_value", ["0", "false", "no", "off", "FALSE"])
def test_flags_parse_falsy_values(monkeypatch, off_value):
    monkeypatch.setenv("PHASE2_ENABLED", off_value)
    assert phase2_enabled() is False
    monkeypatch.setenv("PHASE2_ENABLED", "true")
    assert phase2_enabled() is True


def test_health_reports_feature_flags(monkeypatch):
    for name in ALL_FLAG_ENVS:
        monkeypatch.setenv(name, "false")
    client = TestClient(app_main.app)
    features = client.get("/health").json()["features"]
    assert features == {"rag": False, "phase2": False, "actuals_feedback": False,
                        "llm_explanations": False, "llm_advisory": False}


def test_why_refuses_when_rag_disabled(monkeypatch):
    monkeypatch.setenv("RAG_ENABLED", "false")
    client = TestClient(app_main.app)
    response = client.get("/why/1")
    assert response.status_code == 503
    assert "RAG_ENABLED" in response.json()["detail"]


def test_phase2_endpoints_refuse_when_disabled(monkeypatch):
    monkeypatch.setenv("PHASE2_ENABLED", "false")
    client = TestClient(app_main.app)
    assert client.post("/phase2/interventions/1", json={}).status_code == 503
    assert client.get("/phase2/interventions/does-not-exist").status_code == 503
    assert client.post(
        "/phase2/interventions/does-not-exist/events", json={}
    ).status_code == 503
    assert client.post(
        "/phase2/interventions/does-not-exist/checkpoints", json={}
    ).status_code == 503
    assert client.post(
        "/phase2/interventions/does-not-exist/outcome", json={}
    ).status_code == 503
    assert client.post("/phase2/portfolio/evaluate", json={"store_ids": [1]}).status_code == 503


def test_actuals_replay_refuses_when_feedback_disabled(monkeypatch):
    monkeypatch.setenv("ACTUALS_FEEDBACK_ENABLED", "false")
    with pytest.raises(ValueError, match="ACTUALS_FEEDBACK_ENABLED"):
        app_main._observations_from_actuals(1, 600, datetime.now(timezone.utc))


def test_outcome_body_rejects_wrongly_typed_fields(monkeypatch):
    monkeypatch.setenv("PHASE2_ENABLED", "true")
    monkeypatch.setenv(app_main.APPROVAL_AUTH_TOKEN_ENV, "test-token")
    client = TestClient(app_main.app)
    headers = {"Authorization": "Bearer test-token"}
    # Unknown intervention id: the pydantic body gate runs before the 404,
    # so a structurally invalid body is a 400 even for unknown ids.
    response = client.post(
        "/phase2/interventions/does-not-exist/outcome",
        json={"started_day": "not-an-int"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "invalid outcome input" in response.json()["detail"]


def test_phase2_write_endpoints_require_auth(monkeypatch):
    """Phase-2 state-mutating endpoints are bearer-token gated (fail-closed).

    Writes to the intervention registry and portfolio evaluation used to be
    unauthenticated; anyone who could reach the port could inject intervention
    events or spend forecast-API budget. Same gate as approve/reject.
    """
    monkeypatch.setenv("PHASE2_ENABLED", "true")
    monkeypatch.setenv(app_main.APPROVAL_AUTH_TOKEN_ENV, "test-token")
    client = TestClient(app_main.app)
    wrong = {"Authorization": "Bearer wrong-token"}
    # No token -> 401; wrong token -> 403.
    assert client.post("/phase2/interventions/1", json={}).status_code == 401
    assert client.post(
        "/phase2/interventions/does-not-exist/events", json={}
    ).status_code == 401
    assert client.post(
        "/phase2/interventions/does-not-exist/checkpoints", json={}
    ).status_code == 401
    assert client.post(
        "/phase2/interventions/does-not-exist/outcome",
        json={"started_day": "not-an-int"},
    ).status_code == 401
    assert client.post(
        "/phase2/portfolio/evaluate", json={"store_ids": [1]}
    ).status_code == 401
    assert client.post(
        "/phase2/interventions/1/events", json={}, headers=wrong
    ).status_code == 403


def test_audit_run_schema_rejects_invalid_store_ids():
    from pydantic import ValidationError

    from phase2.schemas import AuditRunPayload

    with pytest.raises(ValidationError):
        AuditRunPayload.model_validate({
            "campaign": "Campaign 18", "timing": "12 PM - 6 PM",
            "run_timestamp": "2025-01-01T00:00:00+00:00", "store_ids": [-1],
        })
    with pytest.raises(ValidationError):
        AuditRunPayload.model_validate({
            "campaign": "Campaign 18", "timing": "12 PM - 6 PM",
            "run_timestamp": "2025-01-01T00:00:00+00:00",
            "store_ids": [1, 1], "extra_field": True,
        })
    # Extra upstream fields are ignored (forward-compatible), not fatal: the
    # live audit API annotates runs with its own operational metadata. Only
    # the consumed 4-field read model is validated and propagated.
    with_extras = AuditRunPayload.model_validate({
        "campaign": "Campaign 18", "timing": "12 PM - 6 PM",
        "run_timestamp": "2025-01-01T00:00:00+00:00", "store_ids": [1, 2],
        "phase": "Pilot", "rollout_decision": "ADVANCE_PHASE",
        "benchmark_sales_uplift": 30.1, "benchmark_ci": [11.9, 51.0],
    })
    assert with_extras.store_ids == [1, 2]
    assert not hasattr(with_extras, "benchmark_sales_uplift")
    valid = AuditRunPayload.model_validate({
        "campaign": "Campaign 18", "timing": "12 PM - 6 PM",
        "run_timestamp": "2025-01-01T00:00:00+00:00", "store_ids": [1, 2],
    })
    assert valid.store_ids == [1, 2]


def test_get_recommendations_is_read_only(tmp_path, monkeypatch):
    """GET /recommendations never evaluates, writes, or spends forecast budget.

    The original side-effecting GET was a real defect (a browser prefetch or a
    monitoring probe triggered external calls and log writes). The read path
    must be a pure view over the persisted log; only the authenticated
    POST /recommendations/run mutates state.
    """
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "recommendation.jsonl"))
    client = TestClient(app_main.app)
    before = (tmp_path / "recommendation.jsonl").exists()
    response = client.get("/recommendations")
    assert response.status_code == 200
    assert response.json()["read_only"] is True
    assert response.json()["recommendations"] == []
    assert (tmp_path / "recommendation.jsonl").exists() is before
    assert client.get("/pending-approvals").json()["count"] == 0


def test_run_and_sweep_endpoints_require_auth(monkeypatch):
    """POST /recommendations/run and POST /monitor/sweep are token-gated.

    Both trigger external forecast calls and persist state, so they are
    explicit authenticated writes like approve/reject (fail-closed: unset
    token -> 503, no header -> 401, wrong token -> 403).
    """
    client = TestClient(app_main.app)
    # Token unset server-side -> fail closed 503.
    assert client.post("/recommendations/run").status_code == 503
    assert client.post("/monitor/sweep").status_code == 503
    monkeypatch.setenv(app_main.APPROVAL_AUTH_TOKEN_ENV, "test-token")
    assert client.post("/recommendations/run").status_code == 401
    assert client.post("/monitor/sweep").status_code == 401
    wrong = {"Authorization": "Bearer wrong-token"}
    assert client.post("/recommendations/run", headers=wrong).status_code == 403
    assert client.post("/monitor/sweep", headers=wrong).status_code == 403
