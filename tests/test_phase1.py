import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

from decision_engine.router import route
from decision_engine.scorer import StoreSignal, compute_recovery_pct, compute_recovery_velocity, score_and_recommend
from decision_engine.verifier import verify_batch, verify_recommendation
from guardrails import requires_human_approval
from memory.history import append_log, read_log
from tools import campaign_tool, forecast_tool


def signal(**overrides):
    values = dict(store_id=1, baseline_forecast=100, current_forecast=110, days_elapsed=10,
                  days_remaining=30, forecast_signal_available=True)
    values.update(overrides)
    return StoreSignal(**values)


def valid_rec(**overrides):
    record = dict(store_id=1, recommendation="CONTINUE", confidence=.8, reason="On track",
                  requires_human_approval=False)
    record.update(overrides)
    return record


def test_router_routes_no_data_near_deadline_and_standard():
    assert route(signal(forecast_signal_available=False)) == "no_data"
    assert route(signal(days_remaining=13)) == "near_deadline"
    assert route(signal(days_remaining=14)) == "standard"


def test_scorer_calculations_and_edge_cases():
    assert compute_recovery_pct(signal()) == 10
    assert compute_recovery_pct(signal(baseline_forecast=0)) == 0
    assert compute_recovery_pct(signal(baseline_forecast=-1)) == 0
    assert compute_recovery_velocity(10, 0) == 0
    assert score_and_recommend(signal(forecast_signal_available=False))["recommendation"] == "NEEDS_REVIEW"


@pytest.mark.parametrize("item,expected", [
    (signal(current_forecast=200), "CONTINUE"),
    (signal(current_forecast=100, days_remaining=10), "ESCALATE"),
    (signal(current_forecast=100, days_remaining=30), "EXTEND_INTERVENTION"),
    (signal(current_forecast=110, days_elapsed=30, days_remaining=30), "MONITOR"),
])
def test_scorer_recommendation_boundaries_and_confidence(item, expected):
    rec = score_and_recommend(item)
    assert rec["recommendation"] == expected
    assert 0 <= rec["confidence"] <= 1


def test_verifier_checks_validity_and_duplicates():
    assert verify_recommendation(valid_rec())["passed"]
    assert not verify_recommendation(valid_rec(recommendation="BAD"))["passed"]
    assert not verify_recommendation(valid_rec(confidence=1.1))["passed"]
    assert not verify_recommendation(valid_rec(reason=""))["passed"]
    assert not verify_recommendation(valid_rec(recommendation="ESCALATE"))["passed"]
    assert not verify_batch([valid_rec(), valid_rec()])["passed"]


@pytest.mark.parametrize("recommendation", ["ESCALATE", "EXTEND_INTERVENTION", "NEEDS_REVIEW"])
def test_guardrails_requires_approval(recommendation):
    assert requires_human_approval(recommendation)


@pytest.mark.parametrize("recommendation", ["CONTINUE", "MONITOR", "UNKNOWN"])
def test_guardrails_does_not_require_approval(recommendation):
    assert not requires_human_approval(recommendation)


def test_campaign_adapter_reads_and_deduplicates(tmp_path, monkeypatch):
    path = tmp_path / "audit_log.jsonl"
    early = {"run_timestamp": "2026-01-01T00:00:00+00:00", "store_ids": [1, 2]}
    late = {"run_timestamp": "2026-02-01T00:00:00+00:00", "store_ids": [2, 3]}
    path.write_text(json.dumps(late) + "\n" + json.dumps(early) + "\n")
    monkeypatch.setenv("CAMPAIGN_AUDIT_LOG_PATH", str(path))
    runs = campaign_tool.get_audit_log()
    assert len(runs) == 2
    assert campaign_tool.get_store_ids_from_audit_log(runs) == [2, 3, 1]
    assert campaign_tool.first_run_for_store(2, runs) == early


def test_campaign_adapter_missing_log_and_malformed_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMPAIGN_AUDIT_LOG_PATH", str(tmp_path / "missing.jsonl"))
    assert campaign_tool.get_audit_log() == []
    path = tmp_path / "audit.jsonl"
    path.write_text("broken\n" + json.dumps({"store_ids": [1]}) + "\n")
    monkeypatch.setenv("CAMPAIGN_AUDIT_LOG_PATH", str(path))
    assert campaign_tool.get_store_ids_from_audit_log(campaign_tool.get_audit_log()) == [1]


def test_memory_append_persists_and_skips_malformed_lines(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "recommendations.jsonl"
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(path))
    append_log({"store_id": 1})
    append_log({"store_id": 2})
    with path.open("a") as log_file:
        log_file.write("not json\n[]\n")
    assert read_log() == [{"store_id": 1}, {"store_id": 2}]


class FakeResponse:
    def __init__(self, payload, status_error=None, json_error=False):
        self.payload, self.status_error, self.json_error = payload, status_error, json_error
    def raise_for_status(self):
        if self.status_error:
            raise self.status_error
    def json(self):
        if self.json_error:
            raise ValueError("bad json")
        return self.payload


def test_forecast_tool_success_and_failures(monkeypatch):
    monkeypatch.setattr(forecast_tool.requests, "get", lambda *a, **k: FakeResponse([{"store_id": 7, "last_day": 4}]))
    monkeypatch.setattr(forecast_tool.requests, "post", lambda *a, **k: FakeResponse({"predicted_sales_value": 42.5}))
    assert forecast_tool.get_store_info(7)["last_day"] == 4
    assert forecast_tool.get_prediction(7, 5) == 42.5
    monkeypatch.setattr(forecast_tool.requests, "get", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError()))
    with pytest.raises(requests.ConnectionError):
        forecast_tool.get_store_info(7)
    monkeypatch.setattr(forecast_tool.requests, "post", lambda *a, **k: FakeResponse({"bad": 1}))
    with pytest.raises(forecast_tool.ForecastResponseError):
        forecast_tool.get_prediction(7, 5)
    monkeypatch.setattr(forecast_tool.requests, "post", lambda *a, **k: FakeResponse({"predicted_sales_value": "42.5"}))
    with pytest.raises(forecast_tool.ForecastResponseError):
        forecast_tool.get_prediction(7, 5)
    monkeypatch.setattr(forecast_tool.requests, "post", lambda *a, **k: FakeResponse(None, json_error=True))
    with pytest.raises(forecast_tool.ForecastResponseError):
        forecast_tool.get_prediction(7, 5)


def test_api_endpoints(tmp_path, monkeypatch):
    pytest.importorskip(
        "fastapi",
        reason="FastAPI endpoint tests require FastAPI/Pydantic; unavailable in this Android Python 3.14 environment.",
    )
    from fastapi.testclient import TestClient
    import app.main as main
    main._pending_approvals.clear()
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "recommendation.jsonl"))
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(main, "get_audit_log", lambda: [{"run_timestamp": now, "store_ids": [1]}])
    monkeypatch.setattr(main, "get_store_info", lambda store_id: {"last_day": 1})
    monkeypatch.setattr(main, "get_prediction", lambda store_id, day: 100)
    client = TestClient(main.app)
    assert client.get("/health").status_code == 200
    response = client.get("/recommendations")
    assert response.status_code == 200
    assert response.json()["recommendations"][0]["recommendation"] == "EXTEND_INTERVENTION"
    assert client.get("/pending-approvals").json()["count"] == 1
    assert client.post("/approve/1").status_code == 200
    assert client.post("/approve/1").status_code == 404
    assert client.get("/log").json()["total_entries"] == 2
    response = client.get("/recommendations")
    assert response.status_code == 200
    assert client.post("/reject/1").status_code == 200



def test_campaign_first_run_validates_timestamps_and_orders_chronologically(caplog):
    earliest_utc = {"run_timestamp": "2026-01-01T01:00:00+01:00", "store_ids": [1]}
    later_utc = {"run_timestamp": "2026-01-01T00:30:00+00:00", "store_ids": [1]}
    malformed_before = {"run_timestamp": "not-a-timestamp", "store_ids": [1]}
    malformed_after = {"run_timestamp": "2026-13-01T00:00:00+00:00", "store_ids": [1]}
    runs = [malformed_before, later_utc, malformed_after, earliest_utc]
    assert campaign_tool.first_run_for_store(1, runs) is earliest_utc
    assert "invalid run_timestamp" in caplog.text


def test_campaign_first_run_returns_none_when_all_timestamps_are_invalid():
    runs = [
        {"run_timestamp": "invalid", "store_ids": [1]},
        {"run_timestamp": "2026-01-01T00:00:00", "store_ids": [1]},
    ]
    assert campaign_tool.first_run_for_store(1, runs) is None


def test_campaign_first_run_keeps_file_order_for_equal_valid_timestamps():
    first = {"run_timestamp": "2026-01-01T00:00:00+00:00", "store_ids": [1], "id": "first"}
    second = {"run_timestamp": "2026-01-01T00:00:00Z", "store_ids": [1], "id": "second"}
    assert campaign_tool.first_run_for_store(1, [first, second]) is first


def test_forecast_tool_http_malformed_store_and_timeout_paths(monkeypatch):
    captured = {}

    def fake_get(*args, **kwargs):
        captured.update(kwargs)
        return FakeResponse({"stores": "not-a-list"})

    monkeypatch.setattr(forecast_tool.requests, "get", fake_get)
    with pytest.raises(forecast_tool.ForecastResponseError):
        forecast_tool.get_store_info(7)
    assert captured["timeout"] == forecast_tool.REQUEST_TIMEOUT_SECONDS

    post_captured = {}
    def fake_post(*args, **kwargs):
        post_captured.update(kwargs)
        return FakeResponse({"predicted_sales_value": 42})

    monkeypatch.setattr(forecast_tool.requests, "post", fake_post)
    assert forecast_tool.get_prediction(7, 5) == 42.0
    assert post_captured["timeout"] == forecast_tool.REQUEST_TIMEOUT_SECONDS

    monkeypatch.setattr(
        forecast_tool.requests,
        "post",
        lambda *args, **kwargs: FakeResponse({}, status_error=requests.HTTPError("503")),
    )
    with pytest.raises(requests.HTTPError):
        forecast_tool.get_prediction(7, 5)

    monkeypatch.setattr(
        forecast_tool.requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout()),
    )
    with pytest.raises(requests.Timeout):
        forecast_tool.get_prediction(7, 5)


def test_api_forecast_statuses_and_error_review_behavior(tmp_path, monkeypatch):
    pytest.importorskip(
        "fastapi",
        reason="FastAPI endpoint tests require FastAPI/Pydantic; unavailable in this Android Python 3.14 environment.",
    )
    from fastapi.testclient import TestClient
    import app.main as main

    main._pending_approvals.clear()
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "recommendation.jsonl"))
    run = {"run_timestamp": datetime.now(timezone.utc).isoformat(), "store_ids": [1]}
    monkeypatch.setattr(main, "get_audit_log", lambda: [run])
    client = TestClient(main.app)

    monkeypatch.setattr(main, "get_store_info", lambda store_id: {"last_day": 1})
    monkeypatch.setattr(main, "get_prediction", lambda store_id, day: 100)
    assert client.get("/recommendations").json()["recommendations"][0]["forecast_status"] == "AVAILABLE"

    monkeypatch.setattr(main, "get_store_info", lambda store_id: None)
    no_data = client.get("/recommendations").json()["recommendations"][0]
    assert no_data["forecast_status"] == "NO_DATA"
    assert no_data["recommendation"] == "NEEDS_REVIEW"

    monkeypatch.setattr(main, "get_store_info", lambda store_id: (_ for _ in ()).throw(requests.ConnectionError("internal detail")))
    network_error = client.get("/recommendations").json()["recommendations"][0]
    assert network_error["forecast_status"] == "ERROR"
    assert network_error["recommendation"] == "NEEDS_REVIEW"
    assert "internal detail" not in str(network_error)

    monkeypatch.setattr(main, "get_store_info", lambda store_id: {"last_day": "bad"})
    malformed = client.get("/recommendations").json()["recommendations"][0]
    assert malformed["forecast_status"] == "ERROR"
    assert malformed["recommendation"] == "NEEDS_REVIEW"



def test_campaign_api_preserves_campaign_as_a_label_and_flags_missing_stable_provenance(monkeypatch):
    requested = {}

    def fake_get(url, **kwargs):
        requested["url"] = url
        requested.update(kwargs)
        return FakeResponse({
            "total_runs": 1,
            "runs": [{
                "campaign": "campaign-api-1",
                "timing": "2026-W01",
                "run_timestamp": "2026-01-01T00:00:00+00:00",
                "store_ids": [7],
            }],
        })

    monkeypatch.setenv("CAMPAIGN_AUDIT_API_URL", campaign_tool.DEFAULT_AUDIT_API_URL)
    monkeypatch.setattr(campaign_tool.requests, "get", fake_get)
    runs = campaign_tool.get_audit_log()
    assert requested == {
        "url": campaign_tool.DEFAULT_AUDIT_API_URL,
        "timeout": campaign_tool.REQUEST_TIMEOUT_SECONDS,
    }
    assert runs == [{
        "campaign_label": "campaign-api-1",
        "campaign_id": None,
        "campaign_provenance_status": campaign_tool.MISSING_STABLE_CAMPAIGN_ID,
        "timing_window": "2026-W01",
        "run_timestamp": "2026-01-01T00:00:00+00:00",
        "store_ids": [7],
    }]
    assert campaign_tool.first_run_for_store(7, runs) == runs[0]


def test_campaign_api_does_not_promote_campaign_18_or_rollout_status_to_delivery_evidence(monkeypatch):
    payload = {
        "total_runs": 1,
        "runs": [{
            "campaign": "Campaign 18",
            "timing": "2026-W01",
            "run_timestamp": "2026-01-01T00:00:00+00:00",
            "store_ids": [7],
            "action": "ADVANCE_PHASE",
            "rollout_status": "SUCCESS",
        }],
    }
    monkeypatch.setenv("CAMPAIGN_AUDIT_API_URL", campaign_tool.DEFAULT_AUDIT_API_URL)
    monkeypatch.setattr(campaign_tool.requests, "get", lambda *args, **kwargs: FakeResponse(payload))
    with pytest.raises(campaign_tool.CampaignAuditResponseError, match="unexpected"):
        campaign_tool.get_audit_log()


@pytest.mark.parametrize("payload", [
    [],
    {"runs": []},
    {"total_runs": 1, "runs": []},
    {"total_runs": 1, "runs": ["not-an-object"]},
])
def test_campaign_api_rejects_schema_invalid_responses(monkeypatch, payload):
    monkeypatch.setenv("CAMPAIGN_AUDIT_API_URL", campaign_tool.DEFAULT_AUDIT_API_URL)
    monkeypatch.setattr(campaign_tool.requests, "get", lambda *args, **kwargs: FakeResponse(payload))
    with pytest.raises(campaign_tool.CampaignAuditResponseError):
        campaign_tool.get_audit_log()


@pytest.mark.parametrize("run", [
    {"campaign": "Campaign 18", "timing": "2026-W01", "run_timestamp": "2026-01-01T00:00:00", "store_ids": [7]},
    {"campaign": "Campaign 18", "timing": "2026-W01", "run_timestamp": "2026-01-01T00:00:00+00:00", "store_ids": [0]},
    {"campaign": "Campaign 18", "timing": " ", "run_timestamp": "2026-01-01T00:00:00+00:00", "store_ids": [7]},
    {"campaign": "Campaign 18", "timing": "2026-W01", "run_timestamp": "2026-01-01T00:00:00+00:00", "store_ids": [7, 7]},
])
def test_campaign_api_strictly_validates_audit_run_fields(monkeypatch, run):
    monkeypatch.setenv("CAMPAIGN_AUDIT_API_URL", campaign_tool.DEFAULT_AUDIT_API_URL)
    monkeypatch.setattr(
        campaign_tool.requests, "get", lambda *args, **kwargs: FakeResponse({"total_runs": 1, "runs": [run]})
    )
    with pytest.raises(campaign_tool.CampaignAuditResponseError):
        campaign_tool.get_audit_log()


@pytest.mark.parametrize("api_url", [
    "https://retail-campaign-automation.onrender.com/run-campaign",
    "https://retail-campaign-automation.onrender.com/advance-phase",
    "https://retail-campaign-automation.onrender.com/rollback-phase",
])
def test_campaign_api_refuses_non_audit_endpoints(monkeypatch, api_url):
    monkeypatch.setenv("CAMPAIGN_AUDIT_API_URL", api_url)
    monkeypatch.setattr(
        campaign_tool.requests,
        "get",
        lambda *args, **kwargs: pytest.fail("non-audit endpoints must never be requested"),
    )
    with pytest.raises(campaign_tool.CampaignAuditResponseError, match="/audit"):
        campaign_tool.get_audit_log()


def test_campaign_api_surfaces_http_and_malformed_json_failures(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_AUDIT_API_URL", campaign_tool.DEFAULT_AUDIT_API_URL)
    monkeypatch.setattr(
        campaign_tool.requests,
        "get",
        lambda *args, **kwargs: FakeResponse({}, status_error=requests.HTTPError("503")),
    )
    with pytest.raises(requests.HTTPError):
        campaign_tool.get_audit_log()
    monkeypatch.setattr(campaign_tool.requests, "get", lambda *args, **kwargs: FakeResponse(None, json_error=True))
    with pytest.raises(campaign_tool.CampaignAuditResponseError):
        campaign_tool.get_audit_log()


def test_campaign_local_jsonl_source_is_unchanged_when_api_is_not_configured(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    path.write_text(json.dumps({"store_ids": [9]}) + "\n")
    monkeypatch.setenv("CAMPAIGN_AUDIT_LOG_PATH", str(path))
    monkeypatch.delenv("CAMPAIGN_AUDIT_API_URL", raising=False)
    monkeypatch.setattr(
        campaign_tool.requests,
        "get",
        lambda *args, **kwargs: pytest.fail("local source must not make an HTTP request"),
    )
    before = path.read_text()
    assert campaign_tool.get_audit_log() == [{"store_ids": [9]}]
    assert path.read_text() == before
