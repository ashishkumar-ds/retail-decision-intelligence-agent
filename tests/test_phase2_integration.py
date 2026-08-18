import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

import app.main as main
from fastapi import HTTPException

from phase2.contracts import InterventionKey
from phase2.registry import InterventionRegistry
import tools.forecast_tool as forecast_tool

pytestmark = pytest.mark.mock


def _key(**overrides):
    values = {
        "store_id": 7,
        "intervention_type": "recovery",
        "target_segment": "loyal",
        "campaign_variant": None,
        "strategy_version": "v1",
    }
    values.update(overrides)
    return values


def _approved_record():
    return {
        "store_id": 7,
        "recommendation_id": "rec-api-1",
        "recommendation": "EXTEND_INTERVENTION",
        "reason": "underperforming",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "approval_id": "approval-api-1",
        "approved": True,
        "decided_at": "2026-01-01T00:01:00+00:00",
    }


@pytest.fixture
def integration_context(tmp_path, monkeypatch):
    registry = InterventionRegistry(tmp_path / "phase2.jsonl")
    monkeypatch.setattr(main, "_phase2_registry", registry)
    monkeypatch.setattr(main, "read_log", lambda: [_approved_record()])
    audit_path = tmp_path / "audit_log.jsonl"
    audit_path.write_text(json.dumps({"campaign_id": "camp-api", "timing_window": "window-api"}) + "\n")
    before = audit_path.read_text()
    monkeypatch.setattr(main, "get_audit_log", lambda: [json.loads(audit_path.read_text())])
    return registry, audit_path, before


def test_approved_recommendation_registers_phase2_intervention(integration_context):
    registry, _, _ = integration_context
    result = main.create_phase2_intervention(7, {"intervention_key": _key()})
    assert result["lifecycle_state"] == "APPROVED"
    snapshot = registry.reconstruct().snapshots[result["intervention_id"]]
    assert snapshot.recommendation_id == "rec-api-1"
    assert snapshot.approval_id == "approval-api-1"


def test_unapproved_recommendation_cannot_register_intervention(integration_context, monkeypatch):
    monkeypatch.setattr(main, "read_log", lambda: [{**_approved_record(), "approved": False}])
    with pytest.raises(HTTPException) as error:
        main.create_phase2_intervention(7, {"intervention_key": _key()})
    assert error.value.status_code == 409


def test_active_and_exact_repetition_guards_are_enforced(integration_context):
    registry, _, _ = integration_context
    first = main.create_phase2_intervention(7, {"intervention_key": _key()})
    with pytest.raises(HTTPException) as active_error:
        main.create_phase2_intervention(7, {"intervention_key": _key(strategy_version="v2")})
    assert active_error.value.status_code == 409
    main.append_phase2_lifecycle_event(first["intervention_id"], {"event_type": "cancel"})
    with pytest.raises(HTTPException) as repetition_error:
        main.create_phase2_intervention(7, {"intervention_key": _key()})
    assert repetition_error.value.status_code == 409
    assert registry.reconstruct().snapshots[first["intervention_id"]].lifecycle_state == "CANCELLED"


def test_reconstruction_works_after_application_registry_reinitialization(integration_context, monkeypatch):
    registry, _, _ = integration_context
    created = main.create_phase2_intervention(7, {"intervention_key": _key()})
    monkeypatch.setattr(main, "_phase2_registry", InterventionRegistry(registry.path))
    result = main.get_phase2_intervention(created["intervention_id"])
    assert result["snapshot"]["lifecycle_state"] == "APPROVED"


def test_phase2_registration_does_not_mutate_project2_audit_file(integration_context):
    _, audit_path, before = integration_context
    main.create_phase2_intervention(7, {"intervention_key": _key()})
    assert audit_path.read_text() == before


def test_outcome_api_preserves_locked_windows_and_rejects_checkpoint_provenance(integration_context):
    registry, _, _ = integration_context
    created = main.create_phase2_intervention(7, {"intervention_key": _key(), "campaign_id": "camp-api"})
    started = datetime.now(timezone.utc) + timedelta(seconds=1)
    completed = started + timedelta(days=30)
    main.append_phase2_lifecycle_event(created["intervention_id"], {"event_type": "start", "occurred_at": started.isoformat()})
    main.append_phase2_lifecycle_event(created["intervention_id"], {"event_type": "complete", "occurred_at": completed.isoformat()})
    checkpoint_result = main.record_phase2_checkpoints(
        created["intervention_id"],
        {
            "as_of": (started + timedelta(days=16)).isoformat(),
            "weeks": 1,
            "observed_checkpoints": [{
                "checkpoint_id": "cp-api",
                "intervention_id": created["intervention_id"],
                "due_at": (started + timedelta(days=7)).isoformat(),
                "observed_at": (started + timedelta(days=7, hours=1)).isoformat(),
                "status": "OBSERVED",
                "campaign_id": "conflicting-campaign",
            }],
        },
    )
    assert checkpoint_result["checkpoints"][0]["status"] == "OBSERVED"
    response = main.evaluate_phase2_outcome(
        created["intervention_id"],
        {
            "as_of": (started + timedelta(days=60)).isoformat(),
            "observations": [
                {"observed_at": (started - timedelta(days=10)).isoformat(), "value": 100},
                {"observed_at": (started + timedelta(days=50)).isoformat(), "value": 130},
            ],
        },
    )
    assert response["evidence_state"] == "CONTRADICTORY"
    assert response["outcome"]["evaluation_due_at"] == (started + timedelta(days=60)).isoformat()
    assert registry.reconstruct().snapshots[created["intervention_id"]].lifecycle_state == "OUTCOME_PENDING"


def test_outcome_api_auto_derives_forecast_reference_and_computes_dual_metrics(integration_context, monkeypatch):
    registry, _, _ = integration_context
    created = main.create_phase2_intervention(7, {"intervention_key": _key(), "campaign_id": "camp-api", "timing_window": "12:00-18:00"})
    started = datetime.now(timezone.utc) + timedelta(seconds=1)
    completed = started + timedelta(days=30)
    main.append_phase2_lifecycle_event(created["intervention_id"], {"event_type": "start", "occurred_at": started.isoformat()})
    main.append_phase2_lifecycle_event(created["intervention_id"], {"event_type": "complete", "occurred_at": completed.isoformat()})

    # Mock Project 1 Forecast API
    monkeypatch.setattr(forecast_tool, "get_store_info", lambda store_id: {"store_id": store_id, "last_day": 100})
    monkeypatch.setattr(main, "get_store_info", lambda store_id: {"store_id": store_id, "last_day": 100})
    queried_days = []
    def fake_prediction(store_id, day, **kwargs):
        queried_days.append((store_id, day))
        return 120.0 # $120/day counterfactual forecast

    monkeypatch.setattr(forecast_tool, "get_prediction", fake_prediction)
    monkeypatch.setattr(main, "get_prediction", fake_prediction)

    response = main.evaluate_phase2_outcome(
        created["intervention_id"],
        {
            "as_of": (started + timedelta(days=60)).isoformat(),
            "observations": [
                {"observed_at": (started - timedelta(days=20)).isoformat(), "value": 100.0, "campaign_id": "camp-api", "timing_window": "12:00-18:00"},
                {"observed_at": (started + timedelta(days=50)).isoformat(), "value": 150.0, "campaign_id": "camp-api", "timing_window": "12:00-18:00"},
            ],
        },
    )

    assert response["evidence_state"] == "SUFFICIENT"
    outcome = response["outcome"]
    # Check 14-day queried horizon
    assert queried_days == [(7, d) for d in range(147, 161)]
    assert outcome["forecast_reference_value"] == 120.0
    assert outcome["forecast_status"] == "AVAILABLE"
    assert outcome["baseline_value"] == 100.0
    assert outcome["recent_observation_value"] == 150.0

    # Longitudinal: (150 - 100) / 100 * 100 = +50.0%
    assert outcome["longitudinal_uplift_pct"] == pytest.approx(50.0)
    assert outcome["actual_uplift_pct"] == pytest.approx(50.0)
    # Counterfactual: (150 - 120) / 120 * 100 = +25.0%
    assert outcome["counterfactual_uplift_pct"] == pytest.approx(25.0)
    assert outcome["recovery_pct_of_target"] == pytest.approx(50.0 / 30.1 * 100)


def test_outcome_api_forecast_error_handling_and_no_dummy_substitution(integration_context, monkeypatch):
    registry, _, _ = integration_context
    created = main.create_phase2_intervention(7, {"intervention_key": _key(), "campaign_id": "camp-api", "timing_window": "12:00-18:00"})
    started = datetime.now(timezone.utc) + timedelta(seconds=1)
    completed = started + timedelta(days=30)
    main.append_phase2_lifecycle_event(created["intervention_id"], {"event_type": "start", "occurred_at": started.isoformat()})
    main.append_phase2_lifecycle_event(created["intervention_id"], {"event_type": "complete", "occurred_at": completed.isoformat()})

    # 1. Store not found in forecast API -> NO_DATA
    monkeypatch.setattr(forecast_tool, "get_store_info", lambda store_id: None)
    monkeypatch.setattr(main, "get_store_info", lambda store_id: None)
    res_no_data = main.evaluate_phase2_outcome(
        created["intervention_id"],
        {
            "as_of": (started + timedelta(days=60)).isoformat(),
            "observations": [
                {"observed_at": (started - timedelta(days=20)).isoformat(), "value": 100.0, "campaign_id": "camp-api", "timing_window": "12:00-18:00"},
                {"observed_at": (started + timedelta(days=50)).isoformat(), "value": 150.0, "campaign_id": "camp-api", "timing_window": "12:00-18:00"},
            ],
        },
    )
    outcome_no_data = res_no_data["outcome"]
    assert outcome_no_data["forecast_status"] == "NO_DATA"
    assert outcome_no_data["forecast_reference_value"] is None
    assert outcome_no_data["counterfactual_uplift_pct"] is None
    assert outcome_no_data["longitudinal_uplift_pct"] == pytest.approx(50.0)

    # 2. Forecast API error -> ERROR
    monkeypatch.setattr(forecast_tool, "get_store_info", lambda store_id: {"store_id": store_id, "last_day": 100})
    monkeypatch.setattr(forecast_tool, "get_prediction", lambda store_id, day: (_ for _ in ()).throw(main.ForecastResponseError("Malformed")))
    monkeypatch.setattr(main, "get_store_info", lambda store_id: {"store_id": store_id, "last_day": 100})
    monkeypatch.setattr(main, "get_prediction", lambda store_id, day: (_ for _ in ()).throw(main.ForecastResponseError("Malformed")))
    res_error = main.evaluate_phase2_outcome(
        created["intervention_id"],
        {
            "as_of": (started + timedelta(days=60)).isoformat(),
            "observations": [
                {"observed_at": (started - timedelta(days=20)).isoformat(), "value": 100.0, "campaign_id": "camp-api", "timing_window": "12:00-18:00"},
                {"observed_at": (started + timedelta(days=50)).isoformat(), "value": 150.0, "campaign_id": "camp-api", "timing_window": "12:00-18:00"},
            ],
        },
    )
    outcome_error = res_error["outcome"]
    assert outcome_error["forecast_status"] == "ERROR"
    assert outcome_error["forecast_reference_value"] is None
    assert outcome_error["counterfactual_uplift_pct"] is None
    assert outcome_error["longitudinal_uplift_pct"] == pytest.approx(50.0)
