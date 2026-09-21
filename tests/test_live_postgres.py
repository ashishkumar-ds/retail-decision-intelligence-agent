"""Live Postgres smoke test — the only test that exercises the real
``DATABASE_URL`` backend end to end.

Skipped unless ``DATABASE_URL`` is set to a postgres/postgresql URL, so the
default offline suite never needs a server. Everything else about the
Postgres path is covered offline in ``tests/test_storage.py`` (wiring) and on
SQLite (the dialect-neutral SQL itself); this test exists to prove the real
thing once, against a real server:

    docker compose --profile postgres up -d postgres
    DATABASE_URL=postgresql://retail:retail@localhost:5432/retail \\
        /usr/bin/python3 -m pytest tests/test_live_postgres.py -q

Run it once against any new deployment target before trusting the store.
"""

import pytest

from storage.database import backend, database_url

pytestmark = pytest.mark.live_api


def _require_postgres_url():
    url = database_url()
    if not url:
        pytest.skip("DATABASE_URL not set — this test exercises a real Postgres server")
    if backend(url) != "postgres":
        pytest.skip(f"DATABASE_URL is not a postgres URL (backend={backend(url)})")
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        try:
            import psycopg  # noqa: F401
        except ImportError:
            pytest.skip("psycopg not installed: pip install '.[storage]'")


def test_live_postgres_migrations_and_store_roundtrip():
    """Migrations apply (twice, idempotently), and the store round-trips."""
    _require_postgres_url()
    from app.state import PendingApprovalStore

    store = PendingApprovalStore()
    store.clear()
    assert len(store) == 0

    store[7] = {"store_id": 7, "recommendation": "MONITOR", "note": "live pg"}
    assert store[7]["recommendation"] == "MONITOR"
    assert 7 in store and store.keys() == [7]
    assert store.pop(7)["store_id"] == 7
    assert len(store) == 0

    # Reconnect: migrations must be a no-op the second time (versioned, once).
    assert store._dialect == "postgres"
    store[8] = {"store_id": 8, "recommendation": "CONTINUE"}
    assert store.items()[0][0] == 8
    store.clear()


def test_live_postgres_schema_migrations_recorded():
    _require_postgres_url()
    from app.state import PendingApprovalStore
    from storage.database import connect, run_migrations

    conn = connect()
    try:
        assert run_migrations(conn, "postgres") == []  # already applied
        applied = {int(r[0]) for r in conn.execute(
            "SELECT version FROM schema_migrations").fetchall()}
        assert applied >= {1}
    finally:
        conn.close()
    assert PendingApprovalStore()._dialect == "postgres"
