import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

import app.main as main
from fastapi import HTTPException

from phase2.contracts import InterventionKey
from phase2.registry import InterventionRegistry


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
    monkeypatch.setattr(main, "get_audit_log", lambda: json.loads(audit_path.read_text()))
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
