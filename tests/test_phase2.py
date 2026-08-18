import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from phase2.contracts import (
    APPROVED,
    ACTIVE,
    CANCELLED,
    COMPLETED,
    CONTRADICTORY,
    DUE,
    EVALUATED,
    FAILED,
    INSUFFICIENT,
    INVALID,
    MISSED,
    NOT_DUE,
    OBSERVED,
    OUTCOME_PENDING,
    PAUSED,
    PARTIAL,
    SUFFICIENT,
    RECOMMENDED,
    REJECTED,
    EXPIRED,
    CheckpointRecord,
    InterventionEvent,
    InterventionKey,
    InterventionRecord,
    InterventionSnapshot,
    OutcomeObservation,
    ApprovalRecord,
    RecommendationRecord,
)
from phase2.evaluator import (
    BASELINE_DAYS,
    EVALUATION_WINDOW_DAYS,
    RECENT_OBSERVATION_DAYS,
    TARGET_UPLIFT_PCT,
    build_intervention_outcome_join,
    build_weekly_checkpoints,
    evaluate_outcome,
)
from phase2.registry import (
    InterventionRegistry,
    active_intervention_guard,
    exact_key_repetition_guard,
    reconstruct_interventions,
    resolve_project2_provenance,
)
from tools import campaign_tool

pytestmark = pytest.mark.mock


def key(**overrides):
    values = dict(
        store_id=7,
        intervention_type="recovery",
        target_segment="loyal",
        campaign_variant=None,
        strategy_version="v1",
    )
    values.update(overrides)
    return InterventionKey(**values)


def dt(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, tzinfo=timezone.utc)


def test_canonical_intervention_key_and_serialization():
    intervention_key = key()
    assert intervention_key.canonical_dict() == {
        "store_id": 7,
        "intervention_type": "recovery",
        "target_segment": "loyal",
        "campaign_variant": None,
        "strategy_version": "v1",
    }
    assert intervention_key.canonical_json() == (
        '{"store_id":7,"intervention_type":"recovery","target_segment":"loyal",'
        '"campaign_variant":null,"strategy_version":"v1"}'
    )
    assert key() == key()
    assert key(target_segment=None, campaign_variant=None) != key(target_segment="loyal")


@pytest.mark.parametrize(
    "overrides",
    [
        {"store_id": True},
        {"intervention_type": ""},
        {"strategy_version": " "},
        {"target_segment": ""},
        {"campaign_variant": ""},
    ],
)
def test_canonical_intervention_key_rejects_invalid_values(overrides):
    with pytest.raises((TypeError, ValueError)):
        key(**overrides)


def test_lifecycle_transitions_and_state_reconstruction():
    intervention_id = "int-1"
    recommendation_id = "rec-1"
    approval_id = "app-1"
    events = [
        InterventionEvent("evt-1", intervention_id, "define", dt(1), key(), recommendation_id=recommendation_id),
        InterventionEvent("evt-2", intervention_id, "approve", dt(2), key(), approval_id=approval_id),
        InterventionEvent("evt-3", intervention_id, "start", dt(3), key()),
        InterventionEvent("evt-4", intervention_id, "pause", dt(4), key()),
        InterventionEvent("evt-5", intervention_id, "resume", dt(5), key()),
        InterventionEvent("evt-6", intervention_id, "complete", dt(6), key()),
        InterventionEvent("evt-7", intervention_id, "outcome_pending", dt(7), key(), outcome_id="out-1"),
        InterventionEvent("evt-8", intervention_id, "evaluate", dt(8), key(), outcome_id="out-1"),
    ]
    result = reconstruct_interventions(events)
    snapshot = result.snapshots[intervention_id]
    assert snapshot.lifecycle_state == EVALUATED
    assert snapshot.started_at == dt(3)
    assert snapshot.ended_at == dt(6)
    assert snapshot.recommendation_id == recommendation_id
    assert snapshot.approval_id == approval_id
    assert snapshot.outcome_id == "out-1"
    assert result.invalid_events == ()


def test_invalid_transitions_are_visible_but_not_applied():
    intervention_id = "int-2"
    events = [
        InterventionEvent("evt-1", intervention_id, "define", dt(1), key(), recommendation_id="rec-2"),
        InterventionEvent("evt-2", intervention_id, "approve", dt(2), key(), approval_id="app-2"),
        InterventionEvent("evt-3", intervention_id, "start", dt(3), key()),
        InterventionEvent("evt-4", intervention_id, "reject", dt(4), key()),
    ]
    result = reconstruct_interventions(events)
    snapshot = result.snapshots[intervention_id]
    assert snapshot.lifecycle_state == ACTIVE
    assert len(result.invalid_events) == 1
    assert "invalid transition" in result.invalid_events[-1].reason


@pytest.mark.parametrize("state", [APPROVED, ACTIVE, PAUSED])
def test_active_intervention_guard_blocks_approved_active_or_paused(state):
    snapshot = InterventionSnapshot(
        intervention_id=f"int-{state.lower()}",
        key=key(),
        lifecycle_state=state,
        recommendation_id="rec-x",
        approval_id="app-x",
        campaign_id=None,
        timing_window=None,
        created_at=dt(1),
        updated_at=dt(1),
        started_at=dt(2) if state in {ACTIVE, PAUSED} else None,
        ended_at=None,
    )
    guard = active_intervention_guard([snapshot], 7)
    assert guard["blocked"] is True
    assert guard["status"] == "ACTIVE_INTERVENTION"


def test_exact_key_repetition_guard_matches_canonical_key_only():
    snapshot = reconstruct_interventions([
        InterventionEvent("evt-1", "int-4", "define", dt(1), key(), recommendation_id="rec-4"),
        InterventionEvent("evt-2", "int-4", "approve", dt(2), key(), approval_id="app-4"),
    ]).snapshots["int-4"]
    guard = exact_key_repetition_guard([snapshot], key())
    assert guard["blocked"] is True
    assert guard["status"] == "REPEATED_INTERVENTION"
    assert exact_key_repetition_guard([snapshot], key(strategy_version="v2"))["blocked"] is False


def test_weekly_checkpoint_lifecycle():
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    observed = CheckpointRecord(
        checkpoint_id="cp-1",
        intervention_id="int-5",
        due_at=started_at + timedelta(days=7),
        observed_at=started_at + timedelta(days=7, hours=2),
        metric_value=10,
        status=DUE,
    )
    checkpoints = build_weekly_checkpoints(
        intervention_id="int-5",
        intervention_started_at=started_at,
        observed_checkpoints=[observed],
        as_of=started_at + timedelta(days=16),
        weeks=2,
    )
    assert [checkpoint.status for checkpoint in checkpoints] == [OBSERVED, MISSED]
    assert checkpoints[0].checkpoint_id == "cp-1"
    assert checkpoints[1].status == MISSED


def test_outcome_evaluator_sufficient_and_insufficient_paths():
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    intervention_key = key()
    observations = [
        OutcomeObservation(started_at - timedelta(days=56) + timedelta(days=1), 100),
        OutcomeObservation(started_at - timedelta(days=56) + timedelta(days=2), 110),
        OutcomeObservation(started_at + timedelta(days=47), 140),
        OutcomeObservation(started_at + timedelta(days=52), 150),
    ]
    result = evaluate_outcome(
        intervention_id="int-6",
        intervention_key=intervention_key,
        intervention_started_at=started_at,
        observations=observations,
        as_of=started_at + timedelta(days=60),
        forecast_reference_value=145.0,
        campaign_id="camp-1",
        timing_window="window-1",
    )
    assert result.evidence_state == "SUFFICIENT"
    assert result.outcome.actual_uplift_pct is not None
    assert result.outcome.recovery_pct_of_target is not None
    assert result.outcome.evaluation_due_at == started_at + timedelta(days=EVALUATION_WINDOW_DAYS)
    assert result.outcome.baseline_window_start == started_at - timedelta(days=BASELINE_DAYS)
    assert result.outcome.recent_window_start == started_at + timedelta(days=EVALUATION_WINDOW_DAYS - RECENT_OBSERVATION_DAYS)
    assert result.outcome.recovery_pct_of_target == pytest.approx(result.outcome.actual_uplift_pct / TARGET_UPLIFT_PCT * 100)

    insufficient = evaluate_outcome(
        intervention_id="int-7",
        intervention_key=intervention_key,
        intervention_started_at=started_at,
        observations=[],
        as_of=started_at + timedelta(days=60),
    )
    assert insufficient.evidence_state == INSUFFICIENT
    assert insufficient.outcome.actual_uplift_pct is None
    assert insufficient.outcome.recovery_pct_of_target is None


def test_outcome_not_due_invalid_and_contradictory_evidence():
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    intervention_key = key()
    not_due = evaluate_outcome(
        intervention_id="int-8",
        intervention_key=intervention_key,
        intervention_started_at=started_at,
        observations=[],
        as_of=started_at + timedelta(days=10),
    )
    assert not_due.evidence_state == NOT_DUE

    invalid = evaluate_outcome(
        intervention_id="int-9",
        intervention_key=intervention_key,
        intervention_started_at=started_at,
        observations=[OutcomeObservation(started_at - timedelta(days=10), 0)],
        as_of=started_at + timedelta(days=60),
    )
    assert invalid.evidence_state in {INVALID, PARTIAL, INSUFFICIENT}

    contradictory = evaluate_outcome(
        intervention_id="int-10",
        intervention_key=intervention_key,
        intervention_started_at=started_at,
        observations=[
            OutcomeObservation(started_at - timedelta(days=10), 100),
            OutcomeObservation(started_at - timedelta(days=10), 101),
            OutcomeObservation(started_at + timedelta(days=50), 120),
        ],
        as_of=started_at + timedelta(days=60),
    )
    assert contradictory.evidence_state == CONTRADICTORY


def test_recommendation_to_outcome_join_and_project2_read_only_boundary(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit_log.jsonl"
    record = {"campaign_id": "camp-7", "timing_window": "window-7", "run_timestamp": "2026-01-01T00:00:00+00:00", "store_ids": [7]}
    audit_path.write_text(json.dumps(record) + "\n")
    monkeypatch.setenv("CAMPAIGN_AUDIT_LOG_PATH", str(audit_path))
    before = audit_path.read_text()
    provenance = resolve_project2_provenance(7, campaign_tool.get_audit_log())
    after = audit_path.read_text()
    assert before == after
    assert provenance == {"campaign_id": "camp-7", "timing_window": "window-7"}

    recommendation = RecommendationRecord(
        recommendation_id="rec-join",
        store_id=7,
        intervention_key=key(),
        recommendation="CONTINUE",
        generated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        reason="on track",
        campaign_id="camp-7",
        timing_window="window-7",
    )
    approval = ApprovalRecord(
        approval_id="app-join",
        recommendation_id="rec-join",
        approved=True,
        decided_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    intervention = InterventionRecord(
        intervention_id="int-join",
        approval_id="app-join",
        recommendation_id="rec-join",
        intervention_key=key(),
        lifecycle_state=APPROVED,
        started_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        campaign_id="camp-7",
        timing_window="window-7",
    )
    checkpoints = [CheckpointRecord("cp-join", "int-join", datetime(2026, 1, 10, tzinfo=timezone.utc), status=OBSERVED)]
    outcome = evaluate_outcome(
        intervention_id="int-join",
        intervention_key=key(),
        intervention_started_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        observations=[
            OutcomeObservation(datetime(2026, 1, 3, tzinfo=timezone.utc) - timedelta(days=10), 100),
            OutcomeObservation(datetime(2026, 2, 28, tzinfo=timezone.utc), 120),
        ],
        as_of=datetime(2026, 3, 4, tzinfo=timezone.utc),
        campaign_id="camp-7",
        timing_window="window-7",
    ).outcome
    join = build_intervention_outcome_join(recommendation, approval, intervention, checkpoints, outcome)
    assert join.evidence_state == "SUFFICIENT"
    assert join.joined is not None
    assert join.joined.recommendation.recommendation_id == "rec-join"
    assert join.joined.approval.approval_id == "app-join"
    assert join.joined.intervention.intervention_id == "int-join"
    assert join.joined.outcome.intervention_id == "int-join"
    assert join.joined.campaign_id == "camp-7"
    assert join.joined.timing_window == "window-7"

    campaign_mismatch = build_intervention_outcome_join(
        recommendation,
        approval,
        intervention,
        checkpoints,
        evaluate_outcome(
            intervention_id="int-join",
            intervention_key=key(),
            intervention_started_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            observations=[
                OutcomeObservation(datetime(2026, 1, 3, tzinfo=timezone.utc) - timedelta(days=10), 100),
                OutcomeObservation(datetime(2026, 2, 28, tzinfo=timezone.utc), 120),
            ],
            as_of=datetime(2026, 3, 4, tzinfo=timezone.utc),
            campaign_id="camp-other",
            timing_window="window-7",
        ).outcome,
    )
    assert campaign_mismatch.evidence_state == CONTRADICTORY
    assert campaign_mismatch.joined is None

    timing_mismatch = build_intervention_outcome_join(
        recommendation,
        approval,
        intervention,
        checkpoints,
        evaluate_outcome(
            intervention_id="int-join",
            intervention_key=key(),
            intervention_started_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            observations=[
                OutcomeObservation(datetime(2026, 1, 3, tzinfo=timezone.utc) - timedelta(days=10), 100),
                OutcomeObservation(datetime(2026, 2, 28, tzinfo=timezone.utc), 120),
            ],
            as_of=datetime(2026, 3, 4, tzinfo=timezone.utc),
            campaign_id="camp-7",
            timing_window="window-other",
        ).outcome,
    )
    assert timing_mismatch.evidence_state == CONTRADICTORY
    assert timing_mismatch.joined is None


def test_malformed_and_contradictory_records_are_visible(tmp_path):
    registry_path = tmp_path / "interventions.jsonl"
    registry = InterventionRegistry(registry_path)
    registry.path.write_text(
        "not json\n"
        + json.dumps({"event_id": "evt-1", "intervention_id": "bad", "event_type": "define", "occurred_at": "2026-01-01T00:00:00Z"})
        + "\n"
    )
    assert registry.read_events() == []

    events = [
        InterventionEvent("evt-1", "int-11", "define", dt(1), key(), recommendation_id="rec-11"),
        InterventionEvent("evt-2", "int-11", "start", dt(2), key()),
        InterventionEvent("evt-3", "int-11", "start", dt(3), key()),
    ]
    result = reconstruct_interventions(events)
    assert result.invalid_events
    assert any("invalid transition" in item.reason for item in result.invalid_events)



def _join_fixture():
    started_at = datetime(2026, 1, 3, tzinfo=timezone.utc)
    recommendation = RecommendationRecord(
        recommendation_id="rec-integrity", store_id=7, intervention_key=key(),
        recommendation="CONTINUE", generated_at=datetime(2026, 1, 2, tzinfo=timezone.utc), reason="on track",
    )
    approval = ApprovalRecord(
        approval_id="app-integrity", recommendation_id="rec-integrity", approved=True,
        decided_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    intervention = InterventionRecord(
        intervention_id="int-integrity", approval_id="app-integrity", recommendation_id="rec-integrity",
        intervention_key=key(), lifecycle_state=COMPLETED, started_at=started_at,
        ended_at=started_at + timedelta(days=30),
    )
    checkpoints = [CheckpointRecord(
        "cp-integrity", "int-integrity", started_at + timedelta(days=7),
        observed_at=started_at + timedelta(days=7), status=OBSERVED,
    )]
    outcome = evaluate_outcome(
        intervention_id="int-integrity", intervention_key=key(), intervention_started_at=started_at,
        observations=[OutcomeObservation(started_at - timedelta(days=10), 100), OutcomeObservation(started_at + timedelta(days=50), 130)],
        as_of=started_at + timedelta(days=60),
    ).outcome
    return recommendation, approval, intervention, checkpoints, outcome


def test_failed_intervention_requires_execution_evidence_before_outcome_pending():
    events = [
        InterventionEvent("failed-define", "int-failed", "define", dt(1), key(), recommendation_id="rec-failed"),
        InterventionEvent("failed-approve", "int-failed", "approve", dt(2), key(), approval_id="app-failed"),
        InterventionEvent("failed-start", "int-failed", "start", dt(3), key()),
        InterventionEvent("failed-fail", "int-failed", "fail", dt(4), key()),
        InterventionEvent("failed-pending", "int-failed", "outcome_pending", dt(5), key()),
    ]
    result = reconstruct_interventions(events)
    assert result.snapshots["int-failed"].lifecycle_state == FAILED
    assert any("lacks valid execution evidence" in item.reason for item in result.invalid_events)
    valid_events = events[:-1] + [InterventionEvent(
        "failed-pending", "int-failed", "outcome_pending", dt(5), key(),
        payload={"execution_evidence": {"source": "project2"}},
    )]
    assert reconstruct_interventions(valid_events).snapshots["int-failed"].lifecycle_state == OUTCOME_PENDING


def test_cancelled_intervention_is_terminal_and_not_outcome_evaluable():
    events = [
        InterventionEvent("cancel-define", "int-cancel", "define", dt(1), key(), recommendation_id="rec-cancel"),
        InterventionEvent("cancel-approve", "int-cancel", "approve", dt(2), key(), approval_id="app-cancel"),
        InterventionEvent("cancel-start", "int-cancel", "start", dt(3), key()),
        InterventionEvent("cancel", "int-cancel", "cancel", dt(4), key()),
        InterventionEvent("cancel-pending", "int-cancel", "outcome_pending", dt(5), key()),
    ]
    result = reconstruct_interventions(events)
    assert result.snapshots["int-cancel"].lifecycle_state == CANCELLED
    assert any("invalid transition" in item.reason for item in result.invalid_events)


def test_replay_detects_duplicate_event_ids_and_identity_provenance_conflicts():
    duplicate = [
        InterventionEvent("same", "int-dup", "define", dt(1), key(), recommendation_id="rec-dup"),
        InterventionEvent("same", "int-dup", "approve", dt(2), key(), approval_id="app-dup"),
    ]
    duplicate_result = reconstruct_interventions(duplicate)
    assert duplicate_result.snapshots["int-dup"].lifecycle_state == RECOMMENDED
    assert any(item.reason == "duplicate event_id" for item in duplicate_result.invalid_events)
    conflicting = [
        InterventionEvent("identity-define", "int-conflict", "define", dt(1), key(), recommendation_id="rec-conflict", campaign_id="camp-1"),
        InterventionEvent("identity-approve", "int-conflict", "approve", dt(2), key(strategy_version="v2"), approval_id="app-conflict", campaign_id="camp-2"),
    ]
    conflict_result = reconstruct_interventions(conflicting)
    assert conflict_result.snapshots["int-conflict"].lifecycle_state == RECOMMENDED
    assert any("conflicts with established" in item.reason for item in conflict_result.invalid_events)


def test_registry_append_rejects_duplicate_event_ids(tmp_path):
    registry = InterventionRegistry(tmp_path / "duplicate.jsonl")
    event = InterventionEvent("append-duplicate", "int-append", "define", dt(1), key(), recommendation_id="rec-append")
    registry.append_event(event)
    with pytest.raises(ValueError, match="duplicate event_id"):
        registry.append_event(event)


def test_registry_reconstruct_preserves_malformed_record_diagnostics(tmp_path):
    registry = InterventionRegistry(tmp_path / "malformed.jsonl")
    registry.path.write_text("not json\n[]\n")
    result = registry.reconstruct()
    assert len(result.invalid_events) == 2
    assert any("malformed JSONL" in item.reason for item in result.invalid_events)
    assert any("object" in item.reason for item in result.invalid_events)


def test_join_rejects_checkpoint_identity_and_provenance_conflicts():
    recommendation, approval, intervention, checkpoints, outcome = _join_fixture()
    assert build_intervention_outcome_join(recommendation, approval, intervention, [replace(checkpoints[0], intervention_id="other-intervention")], outcome).evidence_state == INVALID
    assert build_intervention_outcome_join(recommendation, approval, intervention, [replace(checkpoints[0], intervention_key=key(strategy_version="v2"))], outcome).evidence_state == INVALID
    assert build_intervention_outcome_join(recommendation, approval, intervention, [replace(checkpoints[0], campaign_id="different-campaign")], outcome).evidence_state == CONTRADICTORY


def test_join_rejects_structural_evidence_and_temporal_conflicts():
    recommendation, approval, intervention, checkpoints, outcome = _join_fixture()
    assert build_intervention_outcome_join(recommendation, approval, intervention, checkpoints, replace(outcome, intervention_id="other-intervention")).evidence_state == INVALID
    assert build_intervention_outcome_join(recommendation, replace(approval, approved=False), intervention, checkpoints, outcome).evidence_state == INVALID
    assert build_intervention_outcome_join(recommendation, approval, replace(intervention, lifecycle_state=RECOMMENDED), checkpoints, outcome).evidence_state == INVALID
    insufficient = replace(outcome, evidence_state=INSUFFICIENT, actual_uplift_pct=None, recovery_pct_of_target=None)
    assert build_intervention_outcome_join(recommendation, approval, intervention, checkpoints, insufficient).evidence_state == INSUFFICIENT
    contradictory = replace(outcome, evidence_state=CONTRADICTORY, actual_uplift_pct=None, recovery_pct_of_target=None)
    assert build_intervention_outcome_join(recommendation, approval, intervention, checkpoints, contradictory).evidence_state == CONTRADICTORY
    impossible = replace(approval, decided_at=datetime(2026, 1, 4, tzinfo=timezone.utc))
    assert build_intervention_outcome_join(recommendation, impossible, intervention, checkpoints, outcome).evidence_state == INVALID


def test_valid_complete_join_remains_sufficient():
    recommendation, approval, intervention, checkpoints, outcome = _join_fixture()
    result = build_intervention_outcome_join(recommendation, approval, intervention, checkpoints, outcome)
    assert result.evidence_state == SUFFICIENT
    assert result.joined is not None


def test_campaign_id_normalization_and_timing_window_canonicalization():
    # Campaign normalization
    assert campaign_tool.normalize_campaign_id("Campaign 18") == "18"
    assert campaign_tool.normalize_campaign_id("campaign-18") == "18"
    assert campaign_tool.normalize_campaign_id("campaign_18") == "18"
    assert campaign_tool.normalize_campaign_id("18") == "18"
    assert campaign_tool.normalize_campaign_id(18) == "18"
    assert campaign_tool.normalize_campaign_id("Campaign 1") == "1"
    assert campaign_tool.normalize_campaign_id("campaign-api-1") is None
    assert campaign_tool.normalize_campaign_id("Summer Promo") is None
    assert campaign_tool.normalize_campaign_id("") is None
    assert campaign_tool.normalize_campaign_id("   ") is None
    assert campaign_tool.normalize_campaign_id(None) is None

    # Timing canonicalization
    assert campaign_tool.canonical_timing_window("12 PM - 6 PM") == "12 PM - 6 PM"
    assert campaign_tool.canonical_timing_window("12:00-18:00") == "12 PM - 6 PM"
    assert campaign_tool.canonical_timing_window("12-18") == "12 PM - 6 PM"
    assert campaign_tool.canonical_timing_window("afternoon") == "12 PM - 6 PM"
    assert campaign_tool.canonical_timing_window("1200-1759") == "12 PM - 6 PM"
    assert campaign_tool.canonical_timing_window("2026-W01") == "2026-W01"
    assert campaign_tool.canonical_timing_window(None) is None


def test_dual_uplift_metrics_methodology_and_independence():
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    observations = [
        OutcomeObservation(started_at - timedelta(days=50), 100.0),
        OutcomeObservation(started_at + timedelta(days=50), 130.0),
    ]

    # Without counterfactual reference: only longitudinal uplift
    calc_no_fcst = evaluate_outcome(
        intervention_id="int-dual-1",
        intervention_key=key(),
        intervention_started_at=started_at,
        observations=observations,
        as_of=started_at + timedelta(days=60),
    )
    outcome_1 = calc_no_fcst.outcome
    assert outcome_1.actual_uplift_pct == pytest.approx(30.0)
    assert outcome_1.longitudinal_uplift_pct == pytest.approx(30.0)
    assert outcome_1.counterfactual_uplift_pct is None
    assert "longitudinal_uplift" in outcome_1.methodology
    assert "counterfactual_uplift" in outcome_1.methodology

    # With counterfactual reference: both longitudinal and counterfactual are calculated independently
    calc_with_fcst = evaluate_outcome(
        intervention_id="int-dual-2",
        intervention_key=key(),
        intervention_started_at=started_at,
        observations=observations,
        as_of=started_at + timedelta(days=60),
        forecast_reference_value=100.0, # ML counterfactual predicted $100 vs observed $130 -> +30.0%
    )
    outcome_2 = calc_with_fcst.outcome
    assert outcome_2.longitudinal_uplift_pct == pytest.approx(30.0)
    assert outcome_2.counterfactual_uplift_pct == pytest.approx(30.0)

    # Different baseline ($120) vs forecast ($100) -> distinct metrics
    obs_diff = [
        OutcomeObservation(started_at - timedelta(days=50), 120.0), # baseline $120
        OutcomeObservation(started_at + timedelta(days=50), 150.0), # observed $150
    ]
    calc_diff = evaluate_outcome(
        intervention_id="int-dual-3",
        intervention_key=key(),
        intervention_started_at=started_at,
        observations=obs_diff,
        as_of=started_at + timedelta(days=60),
        forecast_reference_value=100.0, # forecast $100
    )
    outcome_3 = calc_diff.outcome
    # Longitudinal: (150 - 120) / 120 * 100 = +25.0%
    assert outcome_3.longitudinal_uplift_pct == pytest.approx(25.0)
    # Counterfactual: (150 - 100) / 100 * 100 = +50.0%
    assert outcome_3.counterfactual_uplift_pct == pytest.approx(50.0)
    assert outcome_3.longitudinal_uplift_pct != outcome_3.counterfactual_uplift_pct


def test_store_campaign_exposure_derivation_and_metadata():
    from phase2.exposure import compute_store_campaign_eligibility

    txs = [
        # Store 7 transactions during Campaign 18 window (Days 587..642)
        {"STORE_ID": 7, "DAY": 590, "household_key": "hh-1", "SALES_VALUE": 60.0},
        {"STORE_ID": 7, "DAY": 600, "household_key": "hh-2", "SALES_VALUE": 40.0},
        {"STORE_ID": 7, "DAY": 610, "household_key": "hh-unexposed", "SALES_VALUE": 20.0},
        # Other store
        {"STORE_ID": 8, "DAY": 590, "household_key": "hh-1", "SALES_VALUE": 100.0},
        # Outside window
        {"STORE_ID": 7, "DAY": 500, "household_key": "hh-1", "SALES_VALUE": 100.0},
    ]
    campaign_hh = {"hh-1", "hh-2"}

    res = compute_store_campaign_eligibility(
        store_id=7,
        transactions=txs,
        campaign_households=campaign_hh,
        start_day=587,
        end_day=642,
        min_exposed_revenue_share_pct=50.0,
        min_exposed_households=2,
    )

    assert res["store_id"] == 7
    assert res["derivation_type"] == "derived_store_eligibility"
    assert res["direct_store_exposure_logged"] is False
    assert res["total_store_sales"] == 120.0
    assert res["exposed_household_sales"] == 100.0
    assert res["exposed_revenue_share_pct"] == pytest.approx(83.33, abs=0.01)
    assert res["exposed_household_count"] == 2
    assert res["is_eligible"] is True
    assert "household_key" in res["source_fields"]


def test_provenance_join_with_normalized_campaign_and_timing_formats():
    recommendation, approval, intervention, checkpoints, outcome = _join_fixture()

    # recommendation has "Campaign 18", intervention has "18", checkpoint has "18"
    rec_c18 = replace(recommendation, campaign_id="Campaign 18", timing_window="12:00-18:00")
    int_18 = replace(intervention, campaign_id="18", timing_window="12 PM - 6 PM")
    cp_18 = [replace(checkpoints[0], campaign_id="18", timing_window="12 PM - 6 PM")]
    out_18 = replace(outcome, campaign_id="18", timing_window="12 PM - 6 PM")

    result = build_intervention_outcome_join(rec_c18, approval, int_18, cp_18, out_18)
    assert result.evidence_state == SUFFICIENT
    assert result.joined is not None
    assert result.conflicts == ()


def test_forecast_reference_unit_and_horizon_consistency_with_pilot_stores():
    """Verify that daily counterfactual forecast averages match recent observation daily means in scale ($/day)."""
    started_at = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    # 14-day recent daily observations averaging $55/day
    recent_obs = [
        OutcomeObservation(started_at + timedelta(days=47 + i), 55.0)
        for i in range(14)
    ]
    # 56-day baseline daily observations averaging $45/day
    baseline_obs = [
        OutcomeObservation(started_at - timedelta(days=56 - i), 45.0)
        for i in range(56)
    ]

    # Counterfactual 14-day daily mean forecast: $42.0/day
    daily_mean_forecast = 42.0

    calc = evaluate_outcome(
        intervention_id="int-scale-check",
        intervention_key=key(store_id=299),
        intervention_started_at=started_at,
        observations=baseline_obs + recent_obs,
        as_of=started_at + timedelta(days=60),
        forecast_reference_value=daily_mean_forecast,
    )
    outcome = calc.outcome
    assert outcome.baseline_value == pytest.approx(45.0)
    assert outcome.recent_observation_value == pytest.approx(55.0)
    assert outcome.forecast_reference_value == pytest.approx(42.0)

    # Longitudinal uplift: (55 - 45) / 45 * 100 = +22.22%
    assert outcome.longitudinal_uplift_pct == pytest.approx(22.22, abs=0.01)
    # Counterfactual uplift: (55 - 42) / 42 * 100 = +30.95%
    assert outcome.counterfactual_uplift_pct == pytest.approx(30.95, abs=0.01)
    assert outcome.recovery_pct_of_target == pytest.approx(22.222 / 30.1 * 100, abs=0.1)

    # Passing cumulative 56-day 3-store total ($9569) without daily averaging creates unit inconsistency:
    calc_inconsistent = evaluate_outcome(
        intervention_id="int-scale-err",
        intervention_key=key(store_id=299),
        intervention_started_at=started_at,
        observations=baseline_obs + recent_obs,
        as_of=started_at + timedelta(days=60),
        forecast_reference_value=9569.0, # Multi-store 56-day cumulative sum
    )
    # Results in nonsensical negative lift because $55/day is compared to $9569
    assert calc_inconsistent.outcome.counterfactual_uplift_pct == pytest.approx(-99.42, abs=0.01)


def test_evaluate_store_portfolio_multi_store_success_and_error_aggregation(monkeypatch):
    from phase2.portfolio import evaluate_store_portfolio
    import tools.forecast_tool as forecast_tool

    # Mock Forecast API
    def fake_info(store_id):
        if store_id == 999: # Unknown store
            return None
        return {"store_id": store_id, "last_day": 586}

    def fake_pred(store_id, day, **kwargs):
        # Return store-specific daily forecast:
        # Store 299 -> $42/day, Store 317 -> $80/day, Store 448 -> $48/day
        store_base = {299: 42.0, 317: 80.0, 448: 48.0}
        return store_base.get(store_id, 50.0)

    monkeypatch.setattr(forecast_tool, "get_store_info", fake_info)
    monkeypatch.setattr(forecast_tool, "get_prediction", fake_pred)

    # Build simulated transactions for 4 stores
    txs = []
    # Baseline days: 531..586 (56 days), Recent days: 634..647 (14 days)
    for sid, base_sales, recent_sales in [(299, 50.0, 60.0), (317, 70.0, 90.0), (448, 40.0, 55.0), (999, 30.0, 35.0)]:
        for d in range(531, 587):
            txs.append({"STORE_ID": sid, "DAY": d, "SALES_VALUE": base_sales, "household_key": f"hh-{sid}"})
        for d in range(634, 648):
            txs.append({"STORE_ID": sid, "DAY": d, "SALES_VALUE": recent_sales, "household_key": f"hh-{sid}"})

    hh_c18 = {f"hh-299", f"hh-317", f"hh-448", f"hh-999"}

    report = evaluate_store_portfolio(
        store_ids=[299, 317, 448, 999],
        transactions=txs,
        campaign_households=hh_c18,
        campaign_start_day=587,
        campaign_end_day=642,
    )

    assert report.total_stores == 4
    assert report.eligible_stores_count == 4
    assert report.sufficient_evidence_count == 4
    assert report.available_forecast_count == 3
    assert report.error_count == 0

    # Inspect Store 299
    r299 = next(r for r in report.store_results if r.store_id == 299)
    assert r299.baseline_daily_mean == pytest.approx(50.0)
    assert r299.recent_daily_mean == pytest.approx(60.0)
    assert r299.forecast_reference_value == pytest.approx(42.0)
    assert r299.longitudinal_uplift_pct == pytest.approx(20.0) # (60 - 50) / 50 * 100
    assert r299.counterfactual_uplift_pct == pytest.approx(42.86, abs=0.01) # (60 - 42) / 42 * 100
    assert r299.join_state == "SUFFICIENT"

    # Inspect Store 999 (NO_DATA)
    r999 = next(r for r in report.store_results if r.store_id == 999)
    assert r999.forecast_status == "NO_DATA"
    assert r999.forecast_reference_value is None
    assert r999.counterfactual_uplift_pct is None
    assert r999.longitudinal_uplift_pct == pytest.approx(16.67, abs=0.01) # (35 - 30) / 30 * 100

    # Verify portfolio-level aggregations
    assert report.mean_longitudinal_uplift_pct is not None
    assert report.mean_counterfactual_uplift_pct is not None
    assert report.pooled_actual_recent_sales > 0
    assert report.pooled_counterfactual_forecast_sales > 0
    assert report.pooled_counterfactual_uplift_pct is not None
