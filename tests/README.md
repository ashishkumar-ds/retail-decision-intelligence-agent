# Tests

Full suite: `pytest` (423 tests). Endpoint tests need FastAPI/Pydantic; they
skip automatically where those aren't installed.

Two `tests/test_live_postgres.py` tests skip without `DATABASE_URL` (a real
Postgres server). `tests/test_live_api.py` is *not* skipped: it runs against
the deployed Project 1 forecast API and Project 2 campaign-audit API by
default, because live connectivity is part of the contract this suite
verifies. Override `FORECAST_API_URL` / `CAMPAIGN_AUDIT_API_URL` to point at
local instances, or deselect with `-m "not live_api"` to run offline.

## Running on this Termux / Android dev box

The Termux Python (`python`, 3.14) cannot build `pydantic-core`/FastAPI (no
Rust toolchain, and PyPI wheels are glibc-linked). The container's glibc
Python **already has FastAPI/Pydantic installed** - use it, and the endpoint
tests run instead of skipping:

```bash
/usr/bin/python3 -m pytest tests/ -q --ignore=tests/test_live_api.py   # 413 passed
/usr/bin/python3 scripts/check.py                                      # consistency gate
/usr/bin/python3 evaluation/run_evals.py                               # 22 golden + 4 simulator
/usr/local/bin/ruff check .
```

The Termux Python still runs everything that doesn't import `app.main`
(engine, RAG, storage, identity, guardrails, scheduler):
`python -m pytest tests/test_engine.py tests/test_storage.py ...`.

## Hermeticity

Tests must not depend on ambient operator env: flag fixtures scrub
`LLM_*`/`*_ENABLED`, and `tests/test_phase2_integration.py` pins the
forecast-API seam in its shared fixture (an unpinned test there reaches the
real Project 1 API and hangs).
