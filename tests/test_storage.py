"""Tests for the storage layer: backend resolution, versioned migrations,
and the PendingApprovalStore on both its SQLite path and its Postgres path
(psycopg faked - no server needed; the SQL contract is exercised for real
on SQLite, which shares the dialect-neutral DDL and upsert syntax)."""
from __future__ import annotations

import sqlite3
import types

import pytest

import storage.database as db
from app.state import PendingApprovalStore, state_path
from storage.database import backend, run_migrations, translate_placeholders

# --- backend resolution -------------------------------------------------------

def test_backend_default_is_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert backend() == "sqlite"


def test_backend_postgres_urls(monkeypatch):
    for url in ("postgres://u:p@h:5432/db", "postgresql://u:p@h/db?sslmode=require"):
        assert backend(url) == "postgres"


def test_database_url_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    assert db.database_url() == "postgres://u:p@h/db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.database_url() == ""


def test_translate_placeholders():
    assert translate_placeholders("SELECT * FROM t WHERE a=? AND b=?") == \
        "SELECT * FROM t WHERE a=%s AND b=%s"
    assert translate_placeholders("SELECT 1") == "SELECT 1"


# --- migrations ---------------------------------------------------------------

def test_migrations_apply_once(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(str(path))
    assert run_migrations(conn, "sqlite") == [1]
    assert run_migrations(conn, "sqlite") == []
    versions = [r[0] for r in conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [1]
    conn.close()


def test_migration_creates_pending_approvals(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(str(path))
    run_migrations(conn, "sqlite")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pending_approvals)")}
    assert cols == {"store_id", "record", "inserted_at"}
    conn.close()


def test_upsert_dialect_neutral(tmp_path):
    """The seed_from / __setitem__ upsert syntax must run as written on the
    dialect-neutral backend (SQLite proves the SQL shape; PG accepts it too)."""
    path = tmp_path / "state.db"
    conn = sqlite3.connect(str(path))
    run_migrations(conn, "sqlite")
    upsert = ("INSERT INTO pending_approvals(store_id, record, inserted_at) VALUES(?,?,?) "
              "ON CONFLICT(store_id) DO UPDATE SET "
              "record=excluded.record, inserted_at=excluded.inserted_at")
    conn.execute(upsert, (1, '{"a":1}', "now"))
    conn.execute(upsert, (1, '{"a":2}', "now2"))
    conn.commit()
    row = conn.execute(
        "SELECT record FROM pending_approvals WHERE store_id=1").fetchone()[0]
    assert row == '{"a":2}'
    conn.close()


# --- PendingApprovalStore ------------------------------------------------------

def test_store_explicit_path_pins_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    store = PendingApprovalStore(tmp_path / "pinned.db")
    store[1] = {"recommendation": "MONITOR"}
    assert store[1] == {"recommendation": "MONITOR"}
    assert len(store) == 1
    assert 1 in store and 2 not in store
    assert store.pop(1) == {"recommendation": "MONITOR"}
    assert store.pop(1) is None
    assert len(store) == 0


def test_store_default_sqlite_env_path(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "env.db"))
    store = PendingApprovalStore()
    store.seed_from({7: {"store_id": 7, "recommendation": "CONTINUE"}})
    assert store.keys() == [7]
    assert store.items() == [(7, {"store_id": 7, "recommendation": "CONTINUE"})]
    assert state_path().name == "env.db"


def test_store_postgres_backend_via_fake_psycopg(monkeypatch):
    """Full PG path with a fake psycopg2: URL resolution, placeholder
    translation, and migration bookkeeping all run. SQL parity with SQLite is
    proven for real by test_upsert_dialect_neutral (same dialect-neutral DDL
    and upsert syntax); the fake asserts the wiring, not the SQL."""
    executed: list[tuple[str, tuple]] = []
    applied: set[int] = set()

    class FakeCursor:
        def __init__(self, parent):
            self._parent = parent
        def execute(self, sql, params=()):
            if sql.startswith("SELECT version FROM schema_migrations"):
                self._rows = [(v,) for v in sorted(applied)]
            elif sql.startswith("SELECT COUNT(*) FROM pending_approvals"):
                self._rows = [(0,)]
            else:
                self._rows = []
        def fetchall(self):
            return getattr(self, "_rows", [])
        def fetchone(self):
            rows = getattr(self, "_rows", [])
            return rows[0] if rows else None

    class FakeConn:
        def execute(self, sql, params=()):
            cur = FakeCursor(self)
            cur.execute(sql, params)
            if sql.startswith("INSERT INTO schema_migrations"):
                applied.add(int(params[0]))
            executed.append((sql, tuple(params)))
            return cur
        def commit(self):
            return None
        def close(self):
            return None

    fake = types.ModuleType("psycopg2")
    fake.connect = lambda url: FakeConn()  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "psycopg2", fake)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/retail")

    store = PendingApprovalStore()
    len(store)  # triggers _connect: PG connection + migrations
    assert store._dialect == "postgres"
    sqls = [sql for sql, _ in executed]
    assert "CREATE TABLE IF NOT EXISTS schema_migrations" in sqls[0]
    assert any("CREATE TABLE IF NOT EXISTS pending_approvals" in s for s in sqls)
    assert any("%s" in s for s in sqls) and not any("?" in s for s in sqls)
    migration_inserts = [e for e in executed
                         if e[0].startswith("INSERT INTO schema_migrations")]
    assert len(migration_inserts) == 1
    version, applied_at = migration_inserts[0][1]
    assert version == 1 and applied_at  # iso timestamp recorded


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))