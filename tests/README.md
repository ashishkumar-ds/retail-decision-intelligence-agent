# Tests

Full suite: `pytest` (341 tests). Endpoint tests need FastAPI/Pydantic; they
skip automatically where those aren't installed.

## Running on this Termux / Android dev box

The Termux Python (`python`, 3.14) cannot build `pydantic-core`/FastAPI (no
Rust toolchain, and PyPI wheels are glibc-linked). The container's glibc
Python **already has FastAPI/Pydantic installed** - use it, and the endpoint
tests run instead of skipping:

```bash
/usr/bin/python3 -m pytest tests/ -q --ignore=tests/test_live_api.py   # 341 passed
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
