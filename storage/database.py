"""Durable storage layer (storage/).

One place for database backends and schema migrations:

- **Backend resolution** from ``DATABASE_URL``: a ``postgres://`` /
  ``postgresql://`` URL selects Postgres (via psycopg, an optional
  dependency); anything else - including no env at all - keeps the
  project's default embedded SQLite. SQLite remains the boring default:
  no server, WAL journaling, per-operation connections.
- **Versioned migrations** recorded in a ``schema_migrations`` table and
  applied exactly once, in order, inside a transaction. Schema changes are
  code-reviewed in the same commit as the code that needs them (the same
  discipline as the golden-case recalibration rule).

Dialect notes:
- SQL lives in in-repo constants only (never user input), so the ``?`` →
  ``%s`` placeholder translation for Postgres is a controlled, conservative
  transform of statements we wrote.
- ``CREATE TABLE IF NOT EXISTS`` and ``INSERT ... ON CONFLICT ... DO UPDATE``
  are valid in both SQLite (3.24+) and Postgres (9.5+), so the pending-state
  schema is dialect-neutral.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

logger = logging.getLogger("retail_decision_agent.storage")

DATABASE_URL_ENV = "DATABASE_URL"

MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, [
        # Pending-approval state cache (app/state.py). store_id is the key
        # because one store has at most one pending recommendation at a time;
        # the recommendation log (JSONL) remains the system of record.
        "CREATE TABLE IF NOT EXISTS pending_approvals ("
        " store_id    INTEGER PRIMARY KEY,"
        " record      TEXT    NOT NULL,"
        " inserted_at TEXT    NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_pending_approvals_inserted_at"
        " ON pending_approvals (inserted_at)",
    ]),
]

_MIGRATIONS_TABLE = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
)


def database_url() -> str:
    """The configured backend URL; empty means the default SQLite backend."""
    return os.getenv(DATABASE_URL_ENV, "").strip()


def backend(url: str | None = None) -> str:
    """``"postgres"`` for a postgres/postgresql URL, else ``"sqlite"``."""
    url = database_url() if url is None else url.strip()
    return "postgres" if url.startswith(("postgres://", "postgresql://")) else "sqlite"


def translate_placeholders(sql: str) -> str:
    """``?`` → ``%s`` for the psycopg paramstyle (in-repo SQL constants only)."""
    return sql.replace("?", "%s")


def _pg_connect(url: str) -> Any:
    """Open a Postgres connection via psycopg2 or psycopg3 (whichever is installed)."""
    try:
        import psycopg2  # type: ignore
    except ImportError:
        psycopg2 = None
    if psycopg2 is not None:
        return psycopg2.connect(url)
    try:
        import psycopg  # type: ignore
    except ImportError as error:  # pragma: no cover - exercised via fake modules
        raise RuntimeError(
            "DATABASE_URL selects postgres but psycopg is not installed: pip install '.[storage]'"
        ) from error
    return psycopg.connect(url)


def connect(url: str | None = None) -> Any:
    """Open one connection on the resolved backend (call-time resolution, so
    tests and deployments can re-point storage without code changes)."""
    url = database_url() if url is None else url
    if backend(url) == "postgres":
        return _pg_connect(url)
    return sqlite3.connect(url)


def run_migrations(conn: Any, dialect: str) -> list[int]:
    """Apply unapplied migrations, in order, recorded in ``schema_migrations``.

    Idempotent: re-running on a fully migrated database is a no-op that
    returns ``[]``. Each migration runs inside a transaction so a failed
    migration never leaves a half-applied schema (SQLite DDL is transactional;
    Postgres DDL is transactional; both hold here).
    """
    conn.execute(_MIGRATIONS_TABLE)
    row = conn.execute("SELECT version FROM schema_migrations").fetchall()
    applied = {int(r[0]) for r in row}
    ran: list[int] = []
    q = translate_placeholders if dialect == "postgres" else (lambda s: s)
    for version, statements in MIGRATIONS:
        if version in applied:
            continue
        for statement in statements:
            conn.execute(q(statement))
        conn.execute(q("INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)"),
                     (version, _utcnow_iso()))
        conn.commit()
        ran.append(version)
        logger.info("[STORAGE] applied migration %d", version)
    return ran


def _utcnow_iso() -> str:
    from approvals.ledger import utcnow_iso
    return utcnow_iso()