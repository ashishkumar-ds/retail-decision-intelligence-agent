"""Tests for multi-action comparison (decision_engine.simulator.compare_candidate_actions)."""
import pytest

from decision_engine.simulator import compare_candidate_actions


def _obs(n=56, value=100.0, start=594):
    return [{"day": d, "sales_value": value} for d in range(start, start + n)]


def test_compare_ranks_by_margin_and_recommends():
    result = compare_candidate_actions(
        317, _obs(), 650, pre_window_days=56, evaluation_window_days=60,
        margin_rate=0.25,
        campaign_costs={"EXTEND_INTERVENTION": 50.0, "RETARGET_SEGMENT": 200.0,
                        "TIMING_SHIFT": 0.0},
    )
    assert result["evidence_state"] == "SUFFICIENT"
    by_action = {a["action"]: a for a in result["actions"]}
    assert set(by_action) == {"CONTINUE", "EXTEND_INTERVENTION", "RETARGET_SEGMENT",
                              "TIMING_SHIFT", "PAUSE_INTERVENTION"}
    # base_total = 100*60 = 6000; lift 2.84 -> sales 170.4; margin 42.6 - cost
    assert by_action["TIMING_SHIFT"]["expected_incremental_margin"] == pytest.approx(42.6, abs=0.05)
    assert by_action["EXTEND_INTERVENTION"]["expected_incremental_margin"] == pytest.approx(-7.4, abs=0.05)
    assert by_action["CONTINUE"]["expected_incremental_margin"] == pytest.approx(0.0)
    assert by_action["PAUSE_INTERVENTION"]["expected_incremental_margin"] == pytest.approx(0.0)
    assert result["recommended_action"] == "TIMING_SHIFT"
    for action in by_action.values():
        assert action["risk"] in ("reversible", "cautious", "irreversible")
        assert action["confidence"] in (0.8, 0.5, 0.3)


def test_compare_do_nothing_zero_cost_lift():
    result = compare_candidate_actions(
        317, _obs(), 650, pre_window_days=56, evaluation_window_days=60,
        campaign_costs={"CONTINUE": 99.0},  # ignored: do-nothing always costs 0
    )
    by_action = {a["action"]: a for a in result["actions"]}
    assert by_action["CONTINUE"]["budget_impact"] == pytest.approx(0.0)
    assert by_action["CONTINUE"]["expected_lift_pct"] == 0.0


def test_compare_fail_closed_on_sparse_baseline():
    sparse = [{"day": d, "sales_value": 100.0} for d in range(640, 650)]
    result = compare_candidate_actions(
        317, sparse, 650, pre_window_days=56, evaluation_window_days=60)
    assert result["evidence_state"] == "INSUFFICIENT"
    assert "actions" not in result


def test_compare_caller_lifts_override_prior():
    result = compare_candidate_actions(
        317, _obs(), 650, pre_window_days=56, evaluation_window_days=60,
        expected_lifts={"RETARGET_SEGMENT": 5.0},
    )
    by_action = {a["action"]: a for a in result["actions"]}
    assert by_action["RETARGET_SEGMENT"]["expected_lift_pct"] == 5.0
    assert by_action["RETARGET_SEGMENT"]["lift_source"] == "caller-supplied"
    assert result["recommended_action"] == "RETARGET_SEGMENT"


def test_compare_rejects_bad_inputs():
    with pytest.raises(ValueError):
        compare_candidate_actions(317, _obs(), 650, pre_window_days=56,
                                  evaluation_window_days=60, margin_rate=0)
    with pytest.raises(ValueError):
        compare_candidate_actions(317, _obs(), 650, pre_window_days=56,
                                  evaluation_window_days=60,
                                  campaign_costs={"EXTEND_INTERVENTION": -5})
    a = compare_candidate_actions(317, _obs(), 650, pre_window_days=56, evaluation_window_days=60)
    b = compare_candidate_actions(317, _obs(), 650, pre_window_days=56, evaluation_window_days=60)
    assert a == b


def test_compare_endpoint_ranks_actions(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="FastAPI endpoint tests require FastAPI/Pydantic.")
    from fastapi.testclient import TestClient

    import app.main as main

    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "recommendation.jsonl"))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "pending.db"))

    def fake_actuals(store_id, start_day, end_day, **kwargs):
        return {"store_id": store_id, "observations": _obs(56, 100.0, start_day)}

    monkeypatch.setattr(main, "get_actuals", fake_actuals)
    monkeypatch.setattr(main, "get_store_retail_row", lambda sid: None)
    client = TestClient(main.app)
    response = client.get("/simulate/317/compare",
                          params={"started_day": 650, "campaign_cost": 10.0})
    assert response.status_code == 200
    body = response.json()
    assert body["evidence_state"] == "SUFFICIENT"
    assert body["recommended_action"] == "TIMING_SHIFT"
    by_action = {a["action"]: a for a in body["actions"]}
    assert by_action["TIMING_SHIFT"]["expected_incremental_margin"] == pytest.approx(32.6, abs=0.05)
    # No auth: read-only compute must never fail closed on a missing token


def test_compare_endpoint_validates_inputs(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="FastAPI endpoint tests require FastAPI/Pydantic.")
    from fastapi.testclient import TestClient

    import app.main as main

    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "recommendation.jsonl"))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "pending.db"))
    client = TestClient(main.app)
    assert client.get("/simulate/317/compare",
                      params={"started_day": 10}).status_code == 422
    assert client.get("/simulate/317/compare",
                      params={"started_day": 650, "campaign_cost": -1}).status_code == 422
