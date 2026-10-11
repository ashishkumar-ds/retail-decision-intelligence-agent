# Supabase Postgres probe (no new dependency: stdlib + psycopg you already ship).
# Usage:
#   DATABASE_URL='postgresql://postgres.REF:[PASSWORD]@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require' \
#     python scripts/verify_supabase.py
# Exits 0 on success, 1 with the fix on failure. Never prints the password.
"""Verify DATABASE_URL points at Supabase Postgres and the store migrates."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root: storage/

import urllib.parse


def _redacted(url: str) -> str:
    try:
        p = urllib.parse.urlparse(url)
        netloc = p.hostname or "?"
        if p.port:
            netloc += f":{p.port}"
        return f"{p.scheme}://{p.username or '?'}:***@{netloc}{p.path}"
    except Exception:
        return "<unparseable>"


def main() -> int:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        print("FAIL: DATABASE_URL is not set.")
        print("  Fix: Supabase Dashboard -> your project -> Connect -> Postgres")
        print("       -> Session pooler -> copy the string into DATABASE_URL.")
        return 1
    from storage.database import backend

    if backend(url) != "postgres":
        print(f"FAIL: DATABASE_URL does not look like postgres (got {_redacted(url)}).")
        return 1
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if qs.get("sslmode", [""])[0].lower() not in ("require", "verify-full", "verify-ca"):
        print("FAIL: DATABASE_URL is missing ?sslmode=require (Supabase enforces SSL).")
        print("  Fix: append ?sslmode=require (or &sslmode=require) to DATABASE_URL.")
        return 1
    # ponytail: one direct connection per check; no pool (Render opens one per op).
    try:
        from storage.database import connect, run_migrations
    except Exception as error:  # pragma: no cover - import surface
        print(f"FAIL: cannot import storage layer: {error}")
        return 1
    try:
        conn = connect(url)
    except Exception as error:
        hint = str(error).splitlines()[0][:300] if str(error) else type(error).__name__
        print(f"FAIL: cannot connect to {_redacted(url)}: {hint}")
        print("  Fixes: wrong password (Dashboard -> Database -> reset),")
        print("  project paused/ref, or Render egress blocked. See docs/DEPLOYMENT.md.")
        return 1
    try:
        ran = run_migrations(conn, "postgres")
        rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        versions = sorted(int(r[0]) for r in rows)
        print(f"OK: connected {_redacted(url)}; migrations applied-now={ran}; applied={versions}")
        if not versions:
            print("FAIL: schema_migrations is empty after migrate.")
            return 1
    except Exception as error:
        print(f"FAIL: migrations failed: {type(error).__name__}: {str(error)[:300]}")
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
