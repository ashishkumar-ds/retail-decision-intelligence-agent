# Phase 1 Completion Record

## Objective and frozen scope

Phase 1 establishes a runnable, testable, deterministic Retail Decision
Intelligence foundation for Project 3. It deliberately does not implement RAG,
LLM agents, vector retrieval, agentic orchestration, long-term memory,
production authentication, or a persistent approval database.

This document records the approved Phase 1 state. Future work should treat the
application logic, architecture, dependencies, APIs, and tests as frozen until
Phase 2 decisions are made.

## Final architecture

```text
Project 2 audit_log.jsonl (read-only)
        -> campaign adapter -> store signal -> router -> planner -> scorer
        -> verifier -> centralized guardrails -> human approval -> JSONL log
                                      ^
                         forecast API (/stores, /predict)
```

The former `decision engine/` directory was renamed to `decision_engine/` so
its package imports match the implementation. The modules are:

- `decision_engine.router`: routes signals to `no_data`, `near_deadline`, or
  `standard`.
- `decision_engine.planner`: maps a route to deterministic execution steps.
- `decision_engine.scorer`: computes recovery metrics and recommendations.
- `decision_engine.verifier`: checks labels, confidence, reason, approvals,
  and batch duplicate store IDs.
- `guardrails`: the centralized approval policy.
- `tools.campaign_tool`: read-only Project 2 audit-log adapter.
- `tools.forecast_tool`: shared forecast-service adapter.
- `memory.history`: append-only recommendation JSONL history.

## Guardrails and approval state

`ESCALATE`, `EXTEND_INTERVENTION`, and `NEEDS_REVIEW` require human approval.
`CONTINUE` and `MONITOR` do not. This mapping is centralized in `guardrails`.

Pending approvals remain process-local, in-memory state. They are intentionally
separate from the durable recommendation log and do not survive a restart.
Approval and rejection endpoints are not yet authenticated; this is an explicit
Phase 1 security limitation and they must not be publicly exposed without an
appropriate control layer.

## Project 2 campaign audit integration

Project 3 does not import Project 2 code, reproduce campaign logic, create
campaign records, or mutate Project 2 data. `CAMPAIGN_AUDIT_LOG_PATH` locates
Project 2's `audit_log.jsonl`; absent files return no campaign data.

The adapter reads valid JSON object lines only. It accepts integer `store_ids`
and selects the chronologically earliest valid, timezone-aware ISO-8601
`run_timestamp` for a store. Malformed or timezone-naive timestamps are ignored
with a warning; valid source audit content is never rewritten.

The Render Campaign root endpoint used for smoke testing was:

```text
GET https://retail-campaign-automation.onrender.com/
```

It returned status metadata, but does not expose the audit records needed by
`CAMPAIGN_AUDIT_LOG_PATH`. The integration therefore remains file-based and
read-only; no endpoint was inferred or added.

## Forecast API integration

The forecast base URL is configurable through `FORECAST_API_URL`, defaulting
to `https://retail-forecast-api-7sue.onrender.com/`. The adapter uses explicit
10-second timeouts, propagates HTTP/network failures, and rejects malformed or
non-numeric responses.

Endpoints validated:

```text
GET  https://retail-forecast-api-7sue.onrender.com/stores
POST https://retail-forecast-api-7sue.onrender.com/predict
```

The actual deployed `/predict` contract provides the numeric field
`predicted_sales_value` (not `prediction`). Live adapter validation succeeded:

```text
store_id=27, day=642 -> 54.9
```

Recommendation responses expose forecast operational state without internal
exception details:

- `AVAILABLE`: valid forecast metadata and predictions were obtained.
- `NO_DATA`: the forecast service responded successfully but has no store data.
- `ERROR`: network, HTTP, malformed-response, or unexpected forecast failure.

`NO_DATA` and `ERROR` both retain deterministic `NEEDS_REVIEW` behavior while
remaining distinguishable to API consumers.

## Recommendation persistence

`RECOMMENDATION_LOG_PATH` defaults to `logs/recommendation_log.jsonl`.
Recommendations are appended as JSONL, parent directories are created, and the
log survives graceful application restarts. Reading skips malformed or
non-object lines with warnings and does not alter the source log.

## Dependencies

Phase 1 pins the following dependencies for reproducibility on Python 3.11+:

```text
fastapi==0.115.0
uvicorn[standard]==0.30.6
requests==2.32.3
pytest==8.3.3
httpx==0.27.2
```

## Test and validation record

The final deterministic test result was:

```text
21 passed, 2 skipped
```

The skipped endpoint tests require FastAPI/Pydantic. The available Android
Python 3.14 environment could not install the compatible Pydantic core stack,
so FastAPI runtime validation was not claimed. Python 3.11/3.12 runtime
validation has not been performed in this environment.

Validation commands performed:

```bash
python -m py_compile app/main.py decision_engine/*.py guardrails/*.py memory/*.py tools/*.py tests/test_phase1.py
python -m pytest -q
git diff --check
git diff --stat
git status --short
```

## Current Git state

The working tree contains the approved Phase 1 implementation and its
documentation changes, including the package rename represented by deletion of
`decision engine/` and addition of `decision_engine/`. No commit was made and
no push was made.

## Known Phase 1 limitations

- Pending approvals are in-memory and are lost on restart or not shared across
  multiple workers.
- Approval/rejection endpoints have no production authentication or identity
  audit trail.
- JSONL persistence is appropriate for Phase 1 graceful-restart durability,
  not a stronger multi-writer/concurrent persistence guarantee.
- Campaign audit integration requires a file path; the deployed Campaign root
  endpoint cannot currently supply the needed audit data.
- Forecast operations are processed sequentially per evaluated store.
- The deterministic scoring heuristic and confidence are interpretable rules,
  not calibrated probabilities.

## Phase 2 Starting Point

Before Phase 2 implementation, decide:

- whether campaign audit consumption remains file-based or Project 2 should
  expose a deliberately designed, read-only audit API;
- how to validate the FastAPI runtime and endpoint suite on supported Python
  3.11/3.12 environments;
- the production authentication, authorization, and approver identity model
  for approval endpoints;
- whether product requirements justify stronger persistence, lifecycle, and
  concurrency guarantees than Phase 1 JSONL and in-memory pending state.

These are product and operational decisions; they do not authorize changes to
the frozen Phase 1 system.
