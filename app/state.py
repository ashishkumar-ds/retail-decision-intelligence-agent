"""Persistent, thread-safe pending-approval store (SQLite).

Replaces the process-local ``dict`` that previously could not be shared across
uvicorn workers and had to be rebuilt from the whole log on every startup.

Design contract:

- **System of record stays the append-only recommendation log**
  (``memory/history.py``). This store is the durable, indexed *pending-state
  cache* keyed by ``store_id``. ``/pending-approvals``, ``/attention-queue``,
  ``/board`` and the approve/reject paths read and mutate *this* store, so
  multiple workers and the background sweep scheduler share one source of
  truth instead of each holding a private in-memory copy (which split-brained
  across processes and intermittently 404'd approvals in worker B).

- **SQLite is deliberately the boring durable choice**: no new server, WAL
  journaling so concurrent readers run beside the single writer, and a busy
  timeout so lock contention fails after waiting, not instantly.

- **Backend is selectable via ``DATABASE_URL``**: unset (default) keeps the
  embedded SQLite file above; a ``postgres://`` URL runs the same store on
  Postgres via psycopg (``pip install '.[storage]'``), with the schema
  created by the versioned migrations in ``storage/database.py``. An
  explicit ``path`` argument always pins SQLite (tests, composition).

- **One fresh connection per operation.** The store is called from FastAPI's
  sync threadpool and from sweep threads, so each call opens/closes its own
  connection rather than relying on connection-affinity (and avoids
  ``check_same_thread`` hazards). Reconciliation is cheap at this volume.

- **Dict-like interface** (the subset ``app/main.py`` and the tests use):
  ``__getitem__`` / ``__setitem__`` / ``__len__`` / ``__iter__`` / ``pop`` /
  ``values`` / ``keys`` / ``items`` / ``clear`` / ``seed_from``.

The path is read lazily from ``PENDING_APPROVAL_STATE_PATH`` per operation so
tests can point each test at a fresh file and deployments can relocate the
state file without bumping code.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterator, Mapping

from approvals.ledger import utcnow_iso as _utcnow_iso
from storage.database import (
    backend,
    database_url,
    run_migrations,
    translate_placeholders,
)
from storage.database import (
    connect as _storage_connect,
)

DEFAULT_STATE_PATH = Path("logs/pending_approvals.db")
_PATH_ENV = "PENDING_APPROVAL_STATE_PATH"
_BUSY_TIMEOUT_MS = 5000

def state_path() -> Path:
    """Resolve the configured state file path (call-time, so tests override)."""
    return Path(os.getenv(_PATH_ENV, str(DEFAULT_STATE_PATH)))




class PendingApprovalStore:
    """SQLite-backed dictionary of ``store_id`` → recommendation record."""

    def __init__(self, path: str | Path | None = None) -> None:
        # An explicit path pins this instance (mostly for tests/composition);
        # otherwise the path is read from the environment on every operation.
        self._explicit_path = Path(path) if path is not None else None
        self._dialect = "sqlite"

    def _resolve(self) -> Path:
        return self._explicit_path if self._explicit_path is not None else state_path()

    def _connect_sqlite(self, path: Path) -> Any:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_MS / 1000.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        return conn

    def _connect(self) -> Any:
        """One fresh connection per operation, on the resolved backend."""
        if self._explicit_path is not None:
            self._dialect = "sqlite"
            conn = self._connect_sqlite(self._explicit_path)
            run_migrations(conn, "sqlite")
            return conn
        url = database_url()
        if backend(url) == "postgres":
            self._dialect = "postgres"
            conn = _storage_connect(url)
            run_migrations(conn, "postgres")
            return conn
        self._dialect = "sqlite"
        conn = self._connect_sqlite(self._resolve())
        run_migrations(conn, "sqlite")
        return conn

    def _q(self, sql: str) -> str:
        """Dialect paramstyle: ``?`` placeholders natively on SQLite, translated
        to ``%s`` for Postgres (in-repo SQL constants only)."""
        return translate_placeholders(sql) if self._dialect == "postgres" else sql

    # --- mutation -------------------------------------------------------------

    def __setitem__(self, store_id: int, record: Mapping[str, Any]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                self._q(
                "INSERT INTO pending_approvals(store_id, record, inserted_at) VALUES(?,?,?) "
                "ON CONFLICT(store_id) DO UPDATE SET "
                "record=excluded.record, inserted_at=excluded.inserted_at"),
                (store_id, json.dumps(dict(record), default=str, ensure_ascii=False), _utcnow_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def pop(self, store_id: int, default: Any = None) -> Any:
        """Atomically remove and return the record for ``store_id``."""
        conn = self._connect()
        try:
            row = conn.execute(
                self._q("SELECT record FROM pending_approvals WHERE store_id=?"), (store_id,)
            ).fetchone()
            if row is None:
                return default
            conn.execute(self._q("DELETE FROM pending_approvals WHERE store_id=?"), (store_id,))
            conn.commit()
            return json.loads(row[0])
        finally:
            conn.close()

    def clear(self) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM pending_approvals")
            conn.commit()
        finally:
            conn.close()

    def seed_from(self, records: Mapping[int, Mapping[str, Any]]) -> None:
        """Idempotent bulk backfill (upsert) from a ``{store_id: record}`` map.

        Used at startup to derive pending state from the recommendation log
        when the store is first created (the log remains the system of record;
        the store is the derived cache). Safe to call repeatedly.
        """
        if not records:
            return
        conn = self._connect()
        try:
            conn.executemany(
                self._q(
                "INSERT INTO pending_approvals(store_id, record, inserted_at) VALUES(?,?,?) "
                "ON CONFLICT(store_id) DO UPDATE SET "
                "record=excluded.record, inserted_at=excluded.inserted_at"),
                [
                    (sid, json.dumps(dict(rec), default=str, ensure_ascii=False), _utcnow_iso())
                    for sid, rec in records.items()
                ],
            )
            conn.commit()
        finally:
            conn.close()

    # --- reads -----------------------------------------------------------------

    def __getitem__(self, store_id: int) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute(
                self._q("SELECT record FROM pending_approvals WHERE store_id=?"), (store_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(store_id)
        return json.loads(row[0])

    def __contains__(self, store_id: int) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                self._q("SELECT 1 FROM pending_approvals WHERE store_id=?"), (store_id,)
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def __len__(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT COUNT(*) FROM pending_approvals").fetchone()
        finally:
            conn.close()
        return int(row[0])

    def __iter__(self) -> Iterator[int]:
        return iter(self.keys())

    def keys(self) -> list[int]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT store_id FROM pending_approvals ORDER BY store_id"
            ).fetchall()
        finally:
            conn.close()
        return [r[0] for r in rows]

    def values(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT record FROM pending_approvals ORDER BY store_id"
            ).fetchall()
        finally:
            conn.close()
        return [json.loads(r[0]) for r in rows]

    def items(self) -> list[tuple[int, dict[str, Any]]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT store_id, record FROM pending_approvals ORDER BY store_id"
            ).fetchall()
        finally:
            conn.close()
        return [(r[0], json.loads(r[1])) for r in rows]