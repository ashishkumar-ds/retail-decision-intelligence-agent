"""Tests for the durable SQLite pending-approval store (app/state.py).

Covers the dict-like interface app/main.py and the endpoint tests rely on,
plus durability across store instances (the multi-worker/restart contract).
"""
from __future__ import annotations


def test_roundtrip_via_dict_interface(tmp_path):
    from app.state import PendingApprovalStore

    store = PendingApprovalStore(path=tmp_path / "s.db")
    store.clear()
    assert len(store) == 0 and list(iter(store)) == []

    store[1] = {"store_id": 1, "actor": "system:sweep"}
    store[2] = {"store_id": 2}
    assert len(store) == 2
    assert set(iter(store)) == {1, 2}
    assert sorted(store.keys()) == [1, 2]
    assert store[1]["actor"] == "system:sweep"
    assert 1 in store and 999 not in store
    assert len(store.values()) == 2
    assert isinstance(store.values()[0], dict)


def test_pop_is_atomic_and_returns_default_when_missing(tmp_path):
    from app.state import PendingApprovalStore

    store = PendingApprovalStore(path=tmp_path / "s2.db")
    store.clear()
    store[3] = {"store_id": 3}
    assert store.pop(3) == {"store_id": 3}
    assert len(store) == 0
    assert store.pop(999, "miss") == "miss"


def test_seed_from_is_idempotent_upsert(tmp_path):
    from app.state import PendingApprovalStore

    store = PendingApprovalStore(path=tmp_path / "s3.db")
    store.clear()
    store[1] = {"store_id": 1, "recommendation_id": "r-old"}
    store.seed_from({1: {"store_id": 1, "recommendation_id": "r-new"}, 2: {"store_id": 2}})
    assert store[1]["recommendation_id"] == "r-new"  # upsert overwrote
    assert set(iter(store)) == {1, 2}
    store.seed_from({})  # no-op
    assert set(iter(store)) == {1, 2}


def test_durable_across_reopened_store(tmp_path):
    """New store instance on the same file sees previously written state."""
    from app.state import PendingApprovalStore

    path = tmp_path / "s4.db"
    PendingApprovalStore(path=path).clear()
    PendingApprovalStore(path=path)[7] = {"store_id": 7}
    reopened = PendingApprovalStore(path=path)
    assert 7 in reopened
    assert reopened[7] == {"store_id": 7}
    assert len(reopened) == 1


def test_setitem_never_lets_a_fresh_undecided_record_replace_a_decision(tmp_path):
    """The sweep-overwrite race, closed at the store layer.

    A fresh sweep evaluation (no ``decided_at``) must never replace a row that
    carries a human decision, even when the sweep's decided-rows snapshot has
    already gone stale (the approve landed between the snapshot and the
    write). Decided-to-decided and undecided-to-decided writes apply.
    """
    from app.state import PendingApprovalStore

    store = PendingApprovalStore(path=tmp_path / "s5.db")
    store.clear()
    store[9] = {"store_id": 9, "requires_human_approval": True, "recommendation_id": "r1"}
    decided = {
        "store_id": 9, "requires_human_approval": True, "recommendation_id": "r1",
        "decided_at": "2026-10-24T00:00:00+00:00", "approved": True,
        "decided_by": "user:alice",
    }
    store[9] = decided  # approval lands (undecided -> decided: applies)
    # A sweep mid-loop writes its fresh undecided evaluation...
    store[9] = {"store_id": 9, "requires_human_approval": True, "recommendation_id": "r2"}
    assert store[9]["decided_at"] == "2026-10-24T00:00:00+00:00"  # decision survives
    assert store[9]["recommendation_id"] == "r1"
    # Decisions change only through the approve path's atomic pop + reinsert,
    # never by an in-place upsert over a decided row:
    store[9] = {**decided, "decided_by": "user:bob"}
    assert store[9]["decided_by"] == "user:alice"