"""Offline tests for scripts/verify_supabase.py (no server, no secrets)."""

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_supabase.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_supabase", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_redacted_never_leaks_password():
    mod = _load()
    url = "postgresql://postgres.abc:sup3r%23secret@aws-0-x.pooler.supabase.com:5432/postgres?sslmode=require"
    out = mod._redacted(url)
    assert "sup3r" not in out and "secret" not in out
    assert "pooler.supabase.com:5432" in out


def test_probe_fails_closed_without_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=ROOT
    )
    assert proc.returncode == 1
    assert "DATABASE_URL is not set" in proc.stdout


def test_probe_rejects_missing_sslmode(monkeypatch):
    import os

    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://postgres.abc:pw@aws-0-x.pooler.supabase.com:5432/postgres"
    )
    assert os.getenv("DATABASE_URL")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=ROOT
    )
    assert proc.returncode == 1
    assert "sslmode=require" in proc.stdout
    assert "pw" not in proc.stdout  # redaction holds on the failure path too
