# Retail Decision Intelligence Agent

> **Version 4.0** — this release adds the **outcome feedback loop**: evaluated
> intervention outcomes (measured from real observed sales) now feed back into
> the next round of recommendations, closing the
> **plan → execute → measure → re-decide** cycle.

## What's new in v4

- **`GET /actuals/{store_id}` on the Forecast API (Project 1)** — observed daily
  sales from the historical feature store (not predictions), the observed arm of
  the feedback loop. Days without transactions are omitted; coverage gaps are
  evidence, never backfilled.
- **`tools/forecast_tool.get_actuals()`** — typed, fail-closed client for the
  actuals endpoint (envelope validation, store mismatch detection, retries).
- **Replay-mode outcome evaluation** — `POST /phase2/interventions/{id}/outcome`
  accepts `started_day` (dataset DAY index) and derives outcome observations from
  the actuals feed, mapped onto the intervention-relative clock and tagged
  `source="actuals_replay"`. Mixing posted and fetched observations is rejected.
- **Evidence-driven recommendations** — `score_and_recommend()` consumes the
  latest evaluated outcome per store: `NEGATIVE` lift → `PAUSE_INTERVENTION`
  (new, approval-gated); `MEETS_TARGET` → confidence boost; `REVIEW_ZONE` →
  tempered confidence; inconclusive evidence changes nothing but is surfaced.
- **`PAUSE_INTERVENTION`** added to the guardrails' approval-required set.
- **Tests: 88 passed** (80 prior + 8 feedback-loop tests in
  `tests/test_feedback_loop.py`).
- **Project 2 integration fix** — `GET /audit?schema=contract` on the Campaign
  Automation service returns only the 4 contracted fields so the strict HTTP
  audit contract passes (default `/audit` unchanged for n8n/humans); the live
  endpoint-allowlist test now compares URL paths, not raw strings.

## Project Summary

This project extends the **Retail Campaign Automation** system by introducing a deterministic **Retail Decision Intelligence** layer. It uses a **deterministic decision engine** to analyze store performance and generate explainable, evidence-based recommendations for retail decision-making while keeping humans in control of final approvals.

---

## Problem Statement

Project 1 identified the most effective recovery strategy for underperforming stores, while Project 2 automated campaign execution across eligible stores. However, retail managers still need to manually interpret campaign performance, monitor store recovery, and determine the next best action.

**Business Question**

> **How can retail managers receive accurate, explainable, and evidence-based recommendations by combining operational data, business rules, and retail knowledge into a single AI-powered decision intelligence system?**


## Phase 1 status

### Currently implemented

- Deterministic routing, planning, scoring, and verification.
- Shared forecast-service integration (`GET /stores`, `POST /predict`).
- Read-only campaign audit integration, human approval checks, and append-only JSONL recommendation logging.

### Not yet implemented

- RAG, LLM agents, vector retrieval, or agentic tool orchestration.
- Long-term memory and a persistent approval database (approvals persist to the JSONL log, but the pending-approval queue itself is in-memory and lost on restart).

### Approval endpoint auth

`POST /approve/{store_id}` and `POST /reject/{store_id}` require a bearer token: set `APPROVAL_AUTH_TOKEN`
and send `Authorization: Bearer <token>` on each request. If `APPROVAL_AUTH_TOKEN` isn't set, these two
endpoints refuse to serve (503) rather than silently allowing unauthenticated approval/rejection - this is
a single shared token, not per-user accounts, so it's a floor above "anyone who can reach the port," not
production-grade multi-user authorization. Repeat calls for an already-decided store return the existing
decision (200), not a 404, so retried requests don't misread as "this never happened."

## Local setup

Use Python 3.11 or newer. Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload --port 8001
```

`pip install -e ".[dev]"` installs the project itself (via `pyproject.toml`) plus test-only
dependencies, so `pytest` runs directly with no `PYTHONPATH` juggling. Live-API integration
tests are excluded by default (see `pytest.ini`); run them explicitly with `pytest -m live_api`
once `CAMPAIGN_AUDIT_API_URL` is configured and the Forecast API is reachable.

`GET /health` reports service availability. Approval items are held only in the process-local in-memory queue; recommendation history is separately persisted to JSONL and does survive restarts.

## Project 2 integration

Project 3 consumes the `audit_log.jsonl` emitted by Retail Campaign Automation (Project 2) through a read-only adapter. It never imports Project 2 `main.py` and does not recreate campaign records or campaign logic. Configure the Project 2 audit file path before starting Project 3. If the file is absent, Project 3 reports no campaign data rather than manufacturing input.

As an opt-in alternative, set `CAMPAIGN_AUDIT_API_URL=https://retail-campaign-automation.onrender.com/audit`. Project 3 then makes only a `GET` request to that exact `/audit` endpoint; it never calls `/run-campaign`, `/advance-phase`, or `/rollback-phase`. The API must return `{"total_runs": <integer>, "runs": [<objects>]}` with a matching count. Each run must contain only non-empty `campaign` and `timing` text, a timezone-aware ISO-8601 `run_timestamp`, and unique positive integer `store_ids`. `campaign` is always retained verbatim as `campaign_label`. Project 3 additionally derives `campaign_id` from `campaign_label` when the label unambiguously encodes an integer (e.g. `Campaign 18`, `campaign-18`, `18`) — this pattern is verified against the underlying Dunnhumby `campaign_desc.csv`/`campaign_table.csv` ground truth, where campaign identity is always a plain integer with no duplicates or alternate naming. Records with a derived ID are flagged `campaign_provenance_status: NORMALIZED`, not treated as an originally-stable identifier; records whose label doesn't match this pattern (e.g. `Summer Promo`) get `campaign_id: null` and flag `MISSING_STABLE_CAMPAIGN_ID`. Project 3 still has no formal contract with Project 2 guaranteeing this labeling convention holds indefinitely, so no other module treats a `NORMALIZED` `campaign_id` as more authoritative than the `campaign_label` it was derived from. Rollout/action fields, including `ADVANCE_PHASE` and `rollout_status`, are outside this audit schema and cannot establish delivery success. HTTP, JSON, and schema failures are reported as campaign-audit integration errors; no records are invented.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `CAMPAIGN_AUDIT_LOG_PATH` | unset | Path to Project 2 `audit_log.jsonl`; unset/missing means no campaign data. |
| `CAMPAIGN_AUDIT_API_URL` | unset | Opt-in Project 2 read-only audit endpoint. When set, takes precedence over the local JSONL source. |
| `FORECAST_API_URL` | `https://retail-forecast-api-7sue.onrender.com/` | Shared deployed forecast API base URL. |
| `RECOMMENDATION_LOG_PATH` | `logs/recommendation_log.jsonl` | Append-only recommendation JSONL location. |
| `PORT` | `8001` | FastAPI listen port. |

Malformed JSONL lines in either log are skipped with a warning; valid lines remain readable and no source log is changed. Campaign timestamps must be ISO-8601 with a timezone; malformed or timezone-naive timestamps are ignored. Recommendations expose `forecast_status` as `AVAILABLE`, `NO_DATA`, or `ERROR`; technical details are logged but not exposed by the API. Forecast HTTP, network, and malformed-response failures are surfaced as technical failures and are not invented as business data.
