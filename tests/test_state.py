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