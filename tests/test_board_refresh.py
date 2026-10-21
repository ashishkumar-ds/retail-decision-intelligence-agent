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


def test_readable_records_prefers_the_durable_cache_over_a_stale_pending_row(monkeypatch):
    class _Cache:
        def values(self):
            return [{"store_id": 1, "recommendation": "CONTINUE"}]

    class _Pending:
        def values(self):
            return [{"store_id": 1, "recommendation": "EXTEND_INTERVENTION"}]

    monkeypatch.setattr(main, "read_log", lambda: [])
    monkeypatch.setattr(main, "_recommendation_cache", _Cache())
    monkeypatch.setattr(main, "_pending_approvals", _Pending())

    records = main._readable_records()

    assert [rec["recommendation"] for rec in records] == ["CONTINUE"]


def test_persist_caches_every_store_and_drops_recovered_from_the_queue(monkeypatch):
    # A diskless deploy has no log to hold a store that leaves the gate, and
    # the pending queue must not keep offering an approval the engine no longer
    # requires. Every evaluated store therefore lands in the durable cache.
    refreshed = {
        1: {"store_id": 1, "recommendation": "CONTINUE", "requires_human_approval": False},
        2: {"store_id": 2, "recommendation": "EXTEND_INTERVENTION", "requires_human_approval": True},
    }
    appended = []

    class _Cache:
        def __init__(self):
            self.written = {}

        def upsert_many(self, records):
            self.written.update(records)

    cache = _Cache()
    pending = {
        1: {"store_id": 1, "recommendation": "EXTEND_INTERVENTION", "requires_human_approval": True},
        2: {"store_id": 2, "recommendation": "EXTEND_INTERVENTION", "requires_human_approval": True},
    }
    monkeypatch.setattr(main, "evaluate_store", lambda sid, runs, evidence: refreshed[sid])
    monkeypatch.setattr(main, "_outcome_evidence_by_store", lambda: {})
    monkeypatch.setattr(main, "verify_batch", lambda results: {"passed": True})
    monkeypatch.setattr(main, "read_log", lambda: [])
    monkeypatch.setattr(main, "append_log", appended.append)
    monkeypatch.setattr(main, "_recommendation_id", lambda rec: f"id-{rec['store_id']}")
    monkeypatch.setattr(main, "_recommendation_cache", cache)
    monkeypatch.setattr(main, "_pending_approvals", pending)

    results, logged, check = main._evaluate_and_persist_stores([], [1, 2])

    assert len(results) == 2
    assert set(cache.written) == {1, 2}
    assert 1 not in pending
    assert pending[2]["recommendation"] == "EXTEND_INTERVENTION"
    assert len(appended) == 2
    assert logged == 2 and check["passed"] is True


def test_recommendation_cache_roundtrips_on_sqlite(tmp_path):
    from app.state import RecommendationCacheStore

    store = RecommendationCacheStore(path=tmp_path / "state.db")
    store.upsert_many({1: {"store_id": 1, "recommendation": "CONTINUE"}})
    store.upsert({"store_id": 1, "recommendation": "MONITOR"})
    store.upsert_many({2: {"store_id": 2, "recommendation": "NEEDS_REVIEW"}})

    assert len(store) == 2
    assert {rec["store_id"]: rec["recommendation"] for rec in store.values()} == {
        1: "MONITOR",
        2: "NEEDS_REVIEW",
    }


def test_refresh_stale_board_evaluates_the_persisted_cohort(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_board_self_refresh_enabled", lambda: True)
    monkeypatch.setattr(main, "_board_needs_refresh", lambda *args, **kwargs: True)
    monkeypatch.setattr(main, "warm_up", lambda **kwargs: True)
    monkeypatch.setattr(main, "_persisted_audit_runs", lambda: ([{"store_ids": [1]}], [1]))
    monkeypatch.setattr(
        main, "_evaluate_and_persist_stores",
        lambda runs, ids: calls.append((runs, ids)),
    )
    monkeypatch.setattr(main, "_board_refresh_last_attempt", None)

    main._refresh_stale_board()

    assert calls == [([{"store_ids": [1]}], [1])]


def test_board_self_refresh_is_disabled_in_the_suite():
    # conftest pins BOARD_SELF_REFRESH=0: the suite must never fan out over the
    # live forecast service.
    assert main._board_self_refresh_enabled() is False


def test_refresh_defers_when_upstream_is_not_warm(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_board_self_refresh_enabled", lambda: True)
    monkeypatch.setattr(main, "_board_needs_refresh", lambda *args, **kwargs: True)
    monkeypatch.setattr(main, "warm_up", lambda **kwargs: False)
    monkeypatch.setattr(main, "_evaluate_and_persist_stores", lambda *args: calls.append(1))
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
    monkeypatch.setattr(main, "_evaluate_and_persist_stores", lambda *args: calls.append(1))
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

    def boom(runs, ids):
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
