"""Unit tests for the pre-approval intervention simulator.

Covers the simulator contract: deterministic envelope, fail-closed coverage
gate, own-baseline momentum walled off from the causal projection, window
filtering, validation, and the pinned REVIEW_ZONE prior projection.
"""
from __future__ import annotations

import pytest

from decision_engine.simulator import simulate_intervention

PRE, WINDOW = 56, 60


def _obs(days, value=100.0):
    return [{"day": d, "sales_value": value} for d in days]


def _full_window(started_day=650):
    return _obs(range(started_day - PRE, started_day))


def test_full_coverage_projection_is_pinned():
    result = simulate_intervention(317, _full_window(), 650,
                                   pre_window_days=PRE, evaluation_window_days=WINDOW)
    assert result["evidence_state"] == "SUFFICIENT"
    sim = result["simulation"]
    assert sim["baseline"]["mean_daily_sales"] == 100.0
    assert sim["baseline"]["coverage_days"] == 56
    # Prior point 2.84 < target 3.0 -> honest REVIEW_ZONE, scale-up blocked
    projected = sim["projected"]
    assert projected["guardrail"]["assessment_state"] == "REVIEW_ZONE"
    assert projected["guardrail"]["scale_up_eligible"] is False
    # Incremental value: 100/day x 60 days x 2.84% = 170.4
    assert round(projected["incremental_value"]["point"], 6) == 170.4
    assert "REVIEW_ZONE" in result["verdict"]


def test_deterministic_envelope():
    a = simulate_intervention(317, _full_window(), 650,
                              pre_window_days=PRE, evaluation_window_days=WINDOW)
    b = simulate_intervention(317, _full_window(), 650,
                              pre_window_days=PRE, evaluation_window_days=WINDOW)
    assert a == b


def test_insufficient_coverage_is_fail_closed():
    sparse = _obs(range(640, 650))  # 10/56 days
    result = simulate_intervention(317, sparse, 650,
                                   pre_window_days=PRE, evaluation_window_days=WINDOW)
    assert result["evidence_state"] == "INSUFFICIENT"
    assert "coverage" in result["reason"]
    assert "simulation" not in result


def test_momentum_is_reported_but_never_causal():
    obs = [{"day": d, "sales_value": 200.0 if d < 622 else 100.0} for d in range(594, 650)]
    result = simulate_intervention(317, obs, 650,
                                   pre_window_days=PRE, evaluation_window_days=WINDOW)
    momentum = result["simulation"]["own_momentum_pct"]
    assert momentum is not None and momentum["value"] == pytest.approx(-50.0)
    assert "NOT causal" in momentum["note"]
    # The causal projection is unchanged by the store's own decline
    assert result["simulation"]["projected"]["guardrail"]["did_uplift_pct"] == 2.84


def test_out_of_window_days_are_ignored():
    obs = _obs(range(590, 660))  # 6 days before + 10 days after the window
    result = simulate_intervention(317, obs, 650,
                                   pre_window_days=PRE, evaluation_window_days=WINDOW)
    assert result["simulation"]["baseline"]["coverage_days"] == 56


def test_zero_sales_baseline_is_insufficient():
    obs = _obs(range(594, 650), value=0.0)
    result = simulate_intervention(317, obs, 650,
                                   pre_window_days=PRE, evaluation_window_days=WINDOW)
    assert result["evidence_state"] == "INSUFFICIENT"
    assert "no observed sales" in result["reason"]


def test_validation_errors():
    with pytest.raises(TypeError):
        simulate_intervention("317", _full_window(), 650,
                              pre_window_days=PRE, evaluation_window_days=WINDOW)
    with pytest.raises(TypeError):
        simulate_intervention(317, {"not": "a sequence"}, 650,
                              pre_window_days=PRE, evaluation_window_days=WINDOW)
    with pytest.raises(ValueError):
        simulate_intervention(317, _full_window(), 50,  # started_day <= pre_window
                              pre_window_days=PRE, evaluation_window_days=WINDOW)
    with pytest.raises(TypeError):
        simulate_intervention(317, [{"day": 600, "sales_value": "bad"}], 650,
                              pre_window_days=PRE, evaluation_window_days=WINDOW)


def test_api_endpoint(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="FastAPI endpoint tests require FastAPI/Pydantic.")
    from fastapi.testclient import TestClient

    import app.main as main

    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "recommendation.jsonl"))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "pending.db"))

    class FakeActuals(dict):
        pass

    def fake_actuals(store_id, start_day, end_day, **kwargs):
        assert start_day == 650 - 56 and end_day == 649
        return {"store_id": store_id, "observations": _obs(range(start_day, end_day + 1))}

    monkeypatch.setattr(main, "get_actuals", fake_actuals)
    client = TestClient(main.app)
    response = client.get("/simulate/317", params={"started_day": 650})
    assert response.status_code == 200
    body = response.json()
    assert body["evidence_state"] == "SUFFICIENT"
    assert body["simulation"]["projected"]["guardrail"]["assessment_state"] == "REVIEW_ZONE"
    # No auth: read-only compute must never fail closed on a missing token


def test_api_endpoint_service_down(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="FastAPI endpoint tests require FastAPI/Pydantic.")
    import httpx
    from fastapi.testclient import TestClient

    import app.main as main

    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "recommendation.jsonl"))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "pending.db"))

    def down(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(main, "get_actuals", down)
    client = TestClient(main.app)
    response = client.get("/simulate/317", params={"started_day": 650})
    assert response.status_code == 502
