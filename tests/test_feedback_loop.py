"""The convergence test: the loop actually closes.

One test, the whole promise of the project end to end and with real
components (not mocks of our own logic): recommend -> approve -> execute ->
intervene -> measure (observed actuals) -> **re-decide differently**.

Everything external is faked at the edges (forecast API, campaign audit);
every stage in between is the real code path. The assertion that matters is
`test_second_decision_changes_on_measured_evidence`: the second recommendation
differs from the first *because of the measured outcome*, not because of any
other input - health inputs are held identical between the two runs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.mock

STORES = [1]
STARTED_DAY = 1000
BASELINE_VALUE = 100.0
RECENT_VALUE = 80.0   # a 20% drop: measured, negative, unambiguous


def _actuals_envelope(store_id: int, start_day: int, days: int = 117) -> dict:
    """A declining observed-sales series: flat baseline, then a real drop.

    Days >= STARTED_DAY + EVALUATION_WINDOW(60) - RECENT(14) = 1046 fall in
    the evaluator's recent window, so the measured uplift is negative.
    """
    recent_from = start_day + 46
    observations = [
        {"day": day, "sales_value": RECENT_VALUE if day >= recent_from else BASELINE_VALUE}
        for day in range(start_day, start_day + days)
    ]
    return {
        "store_id": store_id,
        "start_day": observations[0]["day"],
        "end_day": observations[-1]["day"],
        "observation_count": len(observations),
        "observations": observations,
    }


@pytest.fixture
def loop(monkeypatch, tmp_path):
    """A fully wired, hermetic agent: real engine, real ledger/registry/journal."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import app.main as main
    from phase2.registry import InterventionRegistry

    now = datetime.now(timezone.utc)
    started_at = now - timedelta(days=61)          # the evaluation window has elapsed

    # Replay/backtest clock: the recommendation is created first, the approval
    # follows (the temporal-join guard enforces that order), and the
    # intervention starts 61 days ago so the evaluation window has elapsed.
    # The registry folds events in occurred_at order, so the create event
    # (stamped at decided_at) must sort before start/complete.
    replay_clock = iter([now - timedelta(days=63), now - timedelta(days=62)])
    monkeypatch.setattr(
        main, "utcnow_iso",
        lambda: next(replay_clock, now - timedelta(days=62)).isoformat())

    # The recommendation's own timestamp comes from the scorer (inline, not
    # via main's clock); pin it so generated_at strictly precedes decided_at -
    # the temporal-join guard enforces that causal order.
    import decision_engine.scorer as _scorer

    class _ReplayDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            stamp = now - timedelta(days=63)
            return stamp.astimezone(tz) if tz else stamp
    monkeypatch.setattr(_scorer, "datetime", _ReplayDatetime)

    registry = InterventionRegistry(tmp_path / "phase2.jsonl")
    monkeypatch.setattr(main, "_phase2_registry", registry)

    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "recommendation_log.jsonl"))
    monkeypatch.setenv("APPROVAL_LEDGER_PATH", str(tmp_path / "approval_ledger.jsonl"))
    monkeypatch.setenv("EXECUTION_JOURNAL_PATH", str(tmp_path / "execution_journal.jsonl"))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "pending.db"))
    monkeypatch.setenv("APPROVAL_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("PHASE2_ENABLED", "true")
    monkeypatch.setenv("ACTUALS_FEEDBACK_ENABLED", "true")
    main._pending_approvals.clear()

    # --- external edges: forecast API and campaign audit ------------------
    run_ts = now.isoformat()
    monkeypatch.setattr(main, "get_audit_log", lambda: [{"run_timestamp": run_ts, "store_ids": STORES}])
    monkeypatch.setattr(main, "get_store_info", lambda store_id: {"store_id": store_id, "last_day": STARTED_DAY})
    monkeypatch.setattr(main, "get_prediction", lambda store_id, day: BASELINE_VALUE)
    def _fake_actuals(store_id, start, end):
        # Mirror the real API contract: the envelope covers exactly the
        # requested range. Baseline days are flat; days in the evaluator's
        # recent window are measured lower.
        recent_from = start_day_ref + 46
        return {
            "store_id": store_id,
            "start_day": start,
            "end_day": end,
            "observation_count": end - start + 1,
            "observations": [
                {"day": day, "sales_value": RECENT_VALUE if day >= recent_from else BASELINE_VALUE}
                for day in range(start, end + 1)
            ],
        }

    start_day_ref = STARTED_DAY
    monkeypatch.setattr(main, "get_actuals", _fake_actuals)

    # Matched-control DiD is additive evidence; hold it unavailable so this test
    # isolates the own-baseline measurement path (fail-open by design).
    def _no_controls(**kwargs):
        raise ValueError("controls unavailable in this test")
    monkeypatch.setattr(main, "get_control_comparison", _no_controls)

    client = TestClient(main.app)
    return {"client": client, "headers": {"Authorization": "Bearer test-token"},
            "started_at": started_at, "main": main}


def test_second_decision_changes_on_measured_evidence(loop):
    client, headers = loop["client"], loop["headers"]
    started_at = loop["started_at"]

    # 1. plan - the engine recommends extending the running intervention.
    first_run = client.post("/recommendations/run", headers=headers)
    assert first_run.status_code == 200
    first_rec = first_run.json()["recommendations"][0]
    assert first_rec["recommendation"] == "EXTEND_INTERVENTION"
    assert first_rec["requires_human_approval"] is True
    assert not first_rec.get("outcome_evidence")

    # 2. human gate - the approval is recorded against the authenticated principal.
    approved = client.post("/approve/1", headers=headers, json={"actor": "manager@example.com"})
    assert approved.status_code == 200
    approval = approved.json()["recommendation"]
    assert approval["decided_by"] == "shared-token"
    assert approval["approved"] is True

    # 3. execute - the gated action runs (dry-run connector, journaled).
    executed = client.post("/execute/1", headers=headers)
    assert executed.status_code == 200
    assert executed.json()["created"] is True
    assert executed.json()["execution"]["recommendation"] == "EXTEND_INTERVENTION"

    # 4. intervene - register the intervention and its lifecycle.
    created = client.post("/phase2/interventions/1", json={
        "intervention_key": {
            "store_id": 1,
            "intervention_type": "recovery",
            "target_segment": "loyal",
            "campaign_variant": None,
            "strategy_version": "v1",
        },
    }, headers=headers)
    assert created.status_code == 200, created.text
    intervention_id = created.json()["intervention_id"]
    for event_type, when in (("start", started_at),
                             ("complete", started_at + timedelta(days=30))):
        event = client.post(f"/phase2/interventions/{intervention_id}/events",
                            json={"event_type": event_type, "occurred_at": when.isoformat()},
                            headers=headers)
        assert event.status_code == 200, event.text

    # 5. measure - evaluate the outcome from OBSERVED sales (replay mode).
    outcome = client.post(
        f"/phase2/interventions/{intervention_id}/outcome",
        json={"as_of": datetime.now(timezone.utc).isoformat(), "started_day": STARTED_DAY},
        headers=headers,
    )
    assert outcome.status_code == 200, outcome.text
    measured = outcome.json()
    assert measured["evidence_state"] == "SUFFICIENT"
    assert measured["outcome"]["target_assessment"] == "NEGATIVE"
    assert measured["outcome"]["actual_uplift_pct"] < 0

    # 6. re-decide - identical health inputs, so any change comes from measurement.
    second_run = client.post("/recommendations/run", headers=headers)
    assert second_run.status_code == 200
    second_rec = second_run.json()["recommendations"][0]
    assert second_rec["recommendation"] == "PAUSE_INTERVENTION"
    assert second_rec["recommendation"] != first_rec["recommendation"]
    # the change is attributable: the reason cites the measured lift
    assert "measured" in second_rec["reason"] and "%" in second_rec["reason"]
    assert second_rec["outcome_evidence"]["actual_uplift_pct"] == measured["outcome"]["actual_uplift_pct"]


def test_execution_can_be_reversed_within_the_loop(loop):
    """The execute stage is reversible: the journal shows the undo."""
    client, headers = loop["client"], loop["headers"]
    client.post("/recommendations/run", headers=headers)
    client.post("/approve/1", headers=headers, json={})
    execution = client.post("/execute/1", headers=headers).json()["execution"]
    reversal = client.post(f"/executions/{execution['execution_id']}/reverse", headers=headers)
    assert reversal.status_code == 200
    assert reversal.json()["execution"]["reversed_at"] is not None
    listed = client.get("/executions", headers=headers).json()
    assert listed["count"] == 1
    assert listed["executions"][0]["reversed_at"] is not None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
