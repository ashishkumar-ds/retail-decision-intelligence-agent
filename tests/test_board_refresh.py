"""Board self-refresh: keep the stakeholder stamp on today, from persisted evidence.

Project 2's audit feed can reset to empty, which used to freeze the board at its
last evaluation day. These tests pin the fallback that reconstructs the cohort
from the board's own records, the day-boundary trigger, and the fail-safe that
keeps a bad upstream from slowing every read.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import app.main as main


def _rec(store_id: int = 1, **overrides) -> dict:
    base = {
        "store_id": store_id,
        "generated_at": "2026-10-13T17:06:48+00:00",
        "days_elapsed": 0,
        "days_remaining": 60,
        "recommendation": "CONTINUE",
        "requires_human_approval": False,
    }
    base.update(overrides)
    return base


def test_persisted_audit_runs_reconstructs_the_campaign_start(monkeypatch):
    monkeypatch.setattr(main, "_readable_records", lambda: [
        _rec(286),
        _rec(27, days_elapsed=2),
    ])

    runs, store_ids = main._persisted_audit_runs()

    assert store_ids == [27, 286]
    by_store = {run["store_ids"][0]: run["run_timestamp"] for run in runs}
    assert by_store[286] == "2026-10-13T17:06:48+00:00"
    assert by_store[27] == "2026-10-11T17:06:48+00:00"


def test_persisted_audit_runs_uses_the_earliest_derived_start(monkeypatch):
    # A refresh re-stamps generated_at; the campaign start must stay pinned to
    # the original record instead of drifting with the evaluation time-of-day.
    monkeypatch.setattr(main, "_readable_records", lambda: [
        _rec(286),
        _rec(286, generated_at="2026-10-22T00:30:00+00:00", days_elapsed=8),
    ])

    runs, store_ids = main._persisted_audit_runs()

    assert store_ids == [286]
    assert runs == [{"store_ids": [286], "run_timestamp": "2026-10-13T17:06:48+00:00"}]


def test_persisted_audit_runs_skips_decided_and_incomplete(monkeypatch):
    monkeypatch.setattr(main, "_readable_records", lambda: [
        _rec(1, decided_at="2026-10-14T00:00:00+00:00"),
        _rec(2, days_elapsed=None),
        _rec(3),
    ])

    runs, store_ids = main._persisted_audit_runs()

    assert store_ids == [3]
    assert runs == [{"store_ids": [3], "run_timestamp": "2026-10-13T17:06:48+00:00"}]


def test_board_needs_refresh_only_when_an_undecided_row_predates_today():
    today = datetime.now(timezone.utc)
    assert main._board_needs_refresh([_rec(1, generated_at=today.isoformat())]) is False
    assert main._board_needs_refresh(
        [_rec(1, generated_at=(today - timedelta(days=1)).isoformat())]
    ) is True
    assert main._board_needs_refresh([]) is False


def test_board_needs_refresh_sees_a_partially_refreshed_cohort():
    # The live failure mode: 54 rows refreshed today, 31 still on the old day.
    # A newest-stamp check called that "fresh"; the board must notice the gap.
    today = datetime.now(timezone.utc)
    records = [
        _rec(1, generated_at=today.isoformat()),
        _rec(2, generated_at=(today - timedelta(days=8)).isoformat()),
    ]
    assert main._board_needs_refresh(records) is True


def test_board_needs_refresh_ignores_decided_rows():
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    decided = _rec(1, generated_at=old, decided_at="2026-10-14T00:00:00+00:00")
    assert main._board_needs_refresh([decided]) is False


def test_board_needs_refresh_retries_after_a_total_forecast_outage():
    # The failure that froze the live board: every store was stamped today with
    # ERROR, so the date-only check called the broken snapshot "fresh".
    today = datetime.now(timezone.utc).isoformat()
    records = [
        _rec(1, generated_at=today, forecast_status="ERROR"),
        _rec(2, generated_at=today, forecast_status="ERROR"),
    ]
    assert main._board_needs_refresh(records) is True


def test_board_needs_refresh_does_not_retry_a_legitimate_no_data_cohort():
    # NO_DATA is the upstream saying "no history for this store", not an outage.
    today = datetime.now(timezone.utc).isoformat()
    records = [_rec(1, generated_at=today, forecast_status="NO_DATA")]
    assert main._board_needs_refresh(records) is False


def test_board_needs_refresh_retries_when_errors_mix_with_no_data():
    # The real cohort is partly uncovered stores (NO_DATA) plus fetch errors;
    # no store got a usable signal, so the refresh should retry.
    today = datetime.now(timezone.utc).isoformat()
    records = [
        _rec(1, generated_at=today, forecast_status="ERROR"),
        _rec(2, generated_at=today, forecast_status="NO_DATA"),
    ]
    assert main._board_needs_refresh(records) is True


def test_board_needs_refresh_does_not_retry_when_a_store_still_has_signal():
    today = datetime.now(timezone.utc).isoformat()
    records = [
        _rec(1, generated_at=today, forecast_status="ERROR"),
        _rec(2, generated_at=today, forecast_status="AVAILABLE"),
    ]
    assert main._board_needs_refresh(records) is False


def test_board_needs_refresh_ignores_decided_outage_rows():
    today = datetime.now(timezone.utc).isoformat()
    decided = _rec(1, generated_at=today, forecast_status="ERROR",
                   decided_at="2026-10-22T00:00:00+00:00")
    assert main._board_needs_refresh([decided]) is False


def test_readable_records_keeps_latest_non_gated_records_from_the_store(monkeypatch):
    # The store is the only durable copy on a diskless deploy; the read union
    # must surface a CONTINUE row, not just the gated queue.
    class _Store:
        def values(self):
            return [{"store_id": 1, "recommendation": "CONTINUE"}]

    monkeypatch.setattr(main, "read_log", lambda: [])
    monkeypatch.setattr(main, "_pending_approvals", _Store())

    records = main._readable_records()

    assert [rec["recommendation"] for rec in records] == ["CONTINUE"]


def test_persist_stores_every_latest_record_and_the_queue_filters_gated(monkeypatch):
    # A diskless deploy has no log to hold a store that leaves the gate. The
    # store keeps every latest record; the approval queue filters to gated rows,
    # so a recovered store no longer offers an approval.
    refreshed = {
        1: {"store_id": 1, "recommendation": "CONTINUE", "requires_human_approval": False},
        2: {"store_id": 2, "recommendation": "EXTEND_INTERVENTION", "requires_human_approval": True},
    }
    appended = []
    pending = {
        1: {"store_id": 1, "recommendation": "EXTEND_INTERVENTION", "requires_human_approval": True},
        2: {"store_id": 2, "recommendation": "EXTEND_INTERVENTION", "requires_human_approval": True},
    }
    monkeypatch.setattr(main, "evaluate_store",
                        lambda sid, runs, evidence, **kwargs: refreshed[sid])
    monkeypatch.setattr(main, "_outcome_evidence_by_store", lambda: {})
    monkeypatch.setattr(main, "verify_batch", lambda results: {"passed": True})
    monkeypatch.setattr(main, "read_log", lambda: [])
    monkeypatch.setattr(main, "append_log", appended.append)
    monkeypatch.setattr(main, "_recommendation_id", lambda rec: f"id-{rec['store_id']}")
    monkeypatch.setattr(main, "_pending_approvals", pending)

    results, logged, check = main._evaluate_and_persist_stores([], [1, 2])

    assert len(results) == 2
    assert pending[1]["recommendation"] == "CONTINUE"
    assert pending[2]["recommendation"] == "EXTEND_INTERVENTION"
    assert [rec["store_id"] for rec in main._gated_pending_records()] == [2]
    assert len(appended) == 2
    assert logged == 2 and check["passed"] is True


def _error_rec(store_id: int) -> dict:
    return {
        "store_id": store_id,
        "recommendation": "NEEDS_REVIEW",
        "forecast_status": "ERROR",
        "requires_human_approval": True,
    }


def _no_data_rec(store_id: int) -> dict:
    return {
        "store_id": store_id,
        "recommendation": "NEEDS_REVIEW",
        "forecast_status": "NO_DATA",
        "requires_human_approval": True,
    }


def test_persist_stores_skips_a_total_forecast_outage(monkeypatch):
    # A failed refresh must not replace the last good latest-per-store snapshot
    # with one ERROR row per store: the board reads latest-per-store, not the
    # append-only history, so overwriting turns the whole board into no-data.
    refreshed = {1: _error_rec(1), 2: _error_rec(2), 3: _no_data_rec(3)}
    appended = []
    previous_log = [{"store_id": 1, "recommendation_id": "old-1"}]
    pending = {1: {"store_id": 1, "recommendation": "CONTINUE",
                   "requires_human_approval": False}}
    monkeypatch.setattr(main, "evaluate_store",
                        lambda sid, runs, evidence, **kwargs: refreshed[sid])
    monkeypatch.setattr(main, "_outcome_evidence_by_store", lambda: {})
    monkeypatch.setattr(main, "verify_batch", lambda results: {"passed": True})
    monkeypatch.setattr(main, "read_log", lambda: list(previous_log))
    monkeypatch.setattr(main, "append_log", appended.append)
    monkeypatch.setattr(main, "_recommendation_id", lambda rec: f"id-{rec['store_id']}")
    monkeypatch.setattr(main, "_pending_approvals", pending)

    results, logged, check = main._evaluate_and_persist_stores([], [1, 2, 3])

    assert len(results) == 3
    assert appended == []
    assert pending == {1: {"store_id": 1, "recommendation": "CONTINUE",
                           "requires_human_approval": False}}
    assert logged == 0
    assert check["persistence_skipped"] == "no_usable_forecast_signal"


def test_persist_stores_persists_a_partial_success(monkeypatch):
    # One usable signal means the upstream is answering; persist the batch and
    # let the individual ERROR row remain visible as a no-data review.
    refreshed = {
        1: {"store_id": 1, "recommendation": "CONTINUE",
            "forecast_status": "AVAILABLE", "requires_human_approval": False},
        2: _error_rec(2),
    }
    appended = []
    pending = {}
    monkeypatch.setattr(main, "evaluate_store",
                        lambda sid, runs, evidence, **kwargs: refreshed[sid])
    monkeypatch.setattr(main, "_outcome_evidence_by_store", lambda: {})
    monkeypatch.setattr(main, "verify_batch", lambda results: {"passed": True})
    monkeypatch.setattr(main, "read_log", lambda: [])
    monkeypatch.setattr(main, "append_log", appended.append)
    monkeypatch.setattr(main, "_recommendation_id", lambda rec: f"id-{rec['store_id']}")
    monkeypatch.setattr(main, "_pending_approvals", pending)

    results, logged, check = main._evaluate_and_persist_stores([], [1, 2])

    assert len(results) == 2
    assert [rec["store_id"] for rec in appended] == [1, 2]
    assert logged == 2
    assert "persistence_skipped" not in check


def test_persist_stores_keeps_a_no_data_cohort(monkeypatch):
    # NO_DATA is a business coverage gap, not an outage; it must still persist.
    refreshed = {1: _no_data_rec(1)}
    appended = []
    pending = {}
    monkeypatch.setattr(main, "evaluate_store",
                        lambda sid, runs, evidence, **kwargs: refreshed[sid])
    monkeypatch.setattr(main, "_outcome_evidence_by_store", lambda: {})
    monkeypatch.setattr(main, "verify_batch", lambda results: {"passed": True})
    monkeypatch.setattr(main, "read_log", lambda: [])
    monkeypatch.setattr(main, "append_log", appended.append)
    monkeypatch.setattr(main, "_recommendation_id", lambda rec: f"id-{rec['store_id']}")
    monkeypatch.setattr(main, "_pending_approvals", pending)

    results, logged, check = main._evaluate_and_persist_stores([], [1])

    assert len(results) == 1
    assert appended == [refreshed[1]]
    assert pending[1] == refreshed[1]
    assert logged == 1
    assert "persistence_skipped" not in check


def test_persist_stores_records_a_first_outage_when_there_is_no_snapshot(monkeypatch):
    # Nothing to protect on a fresh deploy: write the honest no-signal rows so
    # the operator sees the outage instead of an empty board.
    refreshed = {1: _error_rec(1)}
    appended = []
    pending = {}
    monkeypatch.setattr(main, "evaluate_store",
                        lambda sid, runs, evidence, **kwargs: refreshed[sid])
    monkeypatch.setattr(main, "_outcome_evidence_by_store", lambda: {})
    monkeypatch.setattr(main, "verify_batch", lambda results: {"passed": True})
    monkeypatch.setattr(main, "read_log", lambda: [])
    monkeypatch.setattr(main, "append_log", appended.append)
    monkeypatch.setattr(main, "_recommendation_id", lambda rec: f"id-{rec['store_id']}")
    monkeypatch.setattr(main, "_pending_approvals", pending)

    results, logged, check = main._evaluate_and_persist_stores([], [1])

    assert len(results) == 1
    assert appended == [refreshed[1]]
    assert logged == 1
    assert "persistence_skipped" not in check


def test_batch_evaluation_forwards_the_shared_store_map(monkeypatch):
    seen = []
    stores = {1: {"last_day": 10}, 2: {"last_day": 11}}
    refreshed = {
        1: {"store_id": 1, "recommendation": "CONTINUE",
            "forecast_status": "AVAILABLE", "requires_human_approval": False},
        2: {"store_id": 2, "recommendation": "CONTINUE",
            "forecast_status": "AVAILABLE", "requires_human_approval": False},
    }

    def fake_evaluate(sid, runs, evidence, **kwargs):
        seen.append(kwargs.get("stores_by_id"))
        return refreshed[sid]

    monkeypatch.setattr(main, "evaluate_store", fake_evaluate)
    monkeypatch.setattr(main, "_outcome_evidence_by_store", lambda: {})
    monkeypatch.setattr(main, "verify_batch", lambda results: {"passed": True})
    monkeypatch.setattr(main, "read_log", lambda: [])
    monkeypatch.setattr(main, "append_log", lambda rec: None)
    monkeypatch.setattr(main, "_recommendation_id", lambda rec: f"id-{rec['store_id']}")
    monkeypatch.setattr(main, "_pending_approvals", {})

    main._evaluate_and_persist_stores([], [1, 2], stores_by_id=stores)

    assert seen == [stores, stores]


def test_build_store_signal_uses_the_batch_map_without_a_per_store_lookup(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "get_store_info", lambda *args, **kwargs: calls.append(1))
    monkeypatch.setattr(main, "get_prediction", lambda store_id, day: 100.0)
    runs = [{"store_ids": [7], "run_timestamp": "2026-10-13T17:06:48+00:00"}]

    signal = main.build_store_signal(
        7, runs, stores_by_id={7: {"store_id": 7, "last_day": 10}},
    )

    assert signal is not None
    assert signal.forecast_signal_available is True
    assert signal.forecast_status == "AVAILABLE"
    assert calls == []


def test_build_store_signal_missing_from_batch_map_is_no_data(monkeypatch):
    monkeypatch.setattr(main, "get_store_info",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("per-store lookup must not run")))
    runs = [{"store_ids": [8], "run_timestamp": "2026-10-13T17:06:48+00:00"}]

    signal = main.build_store_signal(
        8, runs, stores_by_id={7: {"store_id": 7, "last_day": 10}},
    )

    assert signal is not None
    assert signal.forecast_signal_available is False
    assert signal.forecast_status == "NO_DATA"


def test_refresh_stale_board_evaluates_the_persisted_cohort(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_board_self_refresh_enabled", lambda: True)
    monkeypatch.setattr(main, "_board_needs_refresh", lambda *args, **kwargs: True)
    monkeypatch.setattr(main, "warm_up", lambda **kwargs: True)
    monkeypatch.setattr(main, "_persisted_audit_runs", lambda: ([{"store_ids": [1]}], [1]))
    stores = {1: {"store_id": 1, "last_day": 1}}
    store_calls = []

    def _stores():
        store_calls.append(1)
        return stores

    monkeypatch.setattr(main, "get_all_stores_info", _stores)
    monkeypatch.setattr(
        main, "_evaluate_and_persist_stores",
        lambda runs, ids, **kwargs: calls.append((runs, ids, kwargs)),
    )
    monkeypatch.setattr(main, "_board_refresh_last_attempt", None)

    main._refresh_stale_board()

    assert calls == [([{"store_ids": [1]}], [1], {"stores_by_id": stores})]
    assert store_calls == [1]  # one /stores lookup for the whole cohort


def test_refresh_keeps_the_snapshot_when_the_stores_fetch_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_board_self_refresh_enabled", lambda: True)
    monkeypatch.setattr(main, "_board_needs_refresh", lambda *args, **kwargs: True)
    monkeypatch.setattr(main, "warm_up", lambda **kwargs: True)
    monkeypatch.setattr(main, "_persisted_audit_runs", lambda: ([{"store_ids": [1]}], [1]))

    def _down():
        raise RuntimeError("forecast restarting")

    monkeypatch.setattr(main, "get_all_stores_info", _down)
    monkeypatch.setattr(main, "_evaluate_and_persist_stores",
                        lambda *args, **kwargs: calls.append(1))
    monkeypatch.setattr(main, "_board_refresh_last_attempt", None)

    main._refresh_stale_board()  # must swallow the upstream failure
    main._refresh_stale_board()  # cooldown: no immediate retry

    assert calls == []


def test_board_self_refresh_is_disabled_in_the_suite():
    # conftest pins BOARD_SELF_REFRESH=0: the suite must never fan out over the
    # live forecast service.
    assert main._board_self_refresh_enabled() is False


def test_refresh_defers_when_upstream_is_not_warm(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_board_self_refresh_enabled", lambda: True)
    monkeypatch.setattr(main, "_board_needs_refresh", lambda *args, **kwargs: True)
    monkeypatch.setattr(main, "warm_up", lambda **kwargs: False)
    monkeypatch.setattr(main, "_evaluate_and_persist_stores", lambda *args, **kwargs: calls.append(1))
    monkeypatch.setattr(main, "_board_refresh_last_attempt", None)

    main._refresh_stale_board()

    assert calls == []


def test_first_refresh_is_not_suppressed_on_a_cold_host(monkeypatch):
    # time.monotonic() is small in a fresh container; "never attempted" must not
    # be confused with "attempted at 0" and silently skip the first refresh.
    calls = []
    monkeypatch.setattr(main, "_board_self_refresh_enabled", lambda: True)
    monkeypatch.setattr(main, "_board_needs_refresh", lambda *args, **kwargs: True)
    monkeypatch.setattr(main, "warm_up", lambda **kwargs: True)
    monkeypatch.setattr(main, "_persisted_audit_runs", lambda: ([], [1]))
    monkeypatch.setattr(main, "get_all_stores_info", lambda: {1: {"last_day": 1}})
    monkeypatch.setattr(main, "_evaluate_and_persist_stores", lambda *args, **kwargs: calls.append(1))
    monkeypatch.setattr(main, "_board_refresh_last_attempt", None)
    monkeypatch.setattr(main.time, "monotonic", lambda: 1.0)

    main._refresh_stale_board()

    assert calls == [1]


def test_refresh_failure_keeps_board_and_backs_off(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_board_self_refresh_enabled", lambda: True)
    monkeypatch.setattr(main, "_board_needs_refresh", lambda *args, **kwargs: True)
    monkeypatch.setattr(main, "warm_up", lambda **kwargs: True)
    monkeypatch.setattr(main, "_persisted_audit_runs", lambda: ([], [1]))
    monkeypatch.setattr(main, "get_all_stores_info", lambda: {1: {"last_day": 1}})

    def boom(runs, ids, **kwargs):
        calls.append(1)
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(main, "_evaluate_and_persist_stores", boom)
    monkeypatch.setattr(main, "_board_refresh_last_attempt", None)

    main._refresh_stale_board()  # must swallow the failure and release the lock
    main._refresh_stale_board()  # cooldown: no immediate retry

    assert calls == [1]


def test_board_read_triggers_the_self_refresh_hook(monkeypatch):
    seen = []
    monkeypatch.setattr(main, "_refresh_stale_board", lambda: seen.append(1))
    monkeypatch.setattr(main, "_kick_upstream_warmup", lambda: None)
    monkeypatch.setattr(main, "_readable_records", lambda: [])
    monkeypatch.setattr(main, "root_cause_tagging_enabled", lambda: False)
    main._pending_approvals.clear()

    main.get_status_board()

    assert seen == [1]
