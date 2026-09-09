# Retail Decision Intelligence Agent

> A deterministic decision-intelligence agent for retail store recovery. The
> brain is code, not a model call: every recommendation is recomputable by
> hand, citable by ID, and approved by a human before any budget moves.

## How it is built — one component at a time

The agent is composed from modular components that each own one concern —
memory, tools, planning, decision rules, evaluation, and explanation. The
core design choice is that the decision path is deterministic code: no LLM
ever chooses an action. Its autonomy is a scheduled sweep, and any LLM (off
by default) is quarantined to the explanation layer, where it can only
rephrase an already-grounded answer and can never change a decision.

| # | Component | Module | Task (what it does) |
|---|---|---|---|
| 1 | **The pipeline** | `app/main.py` | Orchestrates route → plan → score → verify → approval gate; FastAPI service; sweep scheduler |
| 2 | **The brain** | `decision_engine/engine.py` | The composed decision pipeline — every component constructor-injected, pure (no I/O, no clock) |
| 3 | **Planning** | `decision_engine/planner.py` | Turns a route into executable steps; `build_plan()` drives execution, not just description |
| 4 | **Decision rules** | `decision_engine/scorer.py` | Recovery %, velocity, health score, decision rule chain, boundary-aware confidence |
| 5 | **Memory** | `memory/history.py` | Append-only JSONL recommendation log; fsync'd, locked, never rewritten |
| 6 | **Event memory** | `phase2/registry.py` | Event-sourced intervention lifecycle (define → start → evaluate) |
| 7 | **Decision ledger** | `approvals/ledger.py` | Double-gated approve/reject record; guardrails + verifier re-run at decision time |
| 8 | **Tools** | `tools/forecast_tool.py`, `tools/campaign_tool.py` | Typed, fail-closed adapters to the forecast + campaign-audit services |
| 9 | **Skills / flows** | `decision_engine/flows/*.md` | Declarative per-route flow specs, drift-checked against the planner |
| 10 | **Trajectory** | `decision_engine/trajectory.py` | One structured per-decision record: every pipeline stage, embedded in the recommendation |
| 11 | **Guardrails** | `guardrails/__init__.py` | Approval-gate set, risk tiers, choice architecture, cost of inaction |
| 12 | **Knowledge (RAG)** | `rag/` | Two-tier grounding: Tier-1 evidence IDs, Tier-2 deterministic BM25 over vetted sources |
| 13 | **Explanation** | `rag/explainer.py`, `rag/llm_explainer.py` | Grounded `why` narrative; the LLM may only rephrase, and must pass the same guards |
| 14 | **Evaluation** | `evaluation/golden_cases.py` + `scripts/check.py` | Pinned business scenarios plus cross-module drift gates in CI |
| 15 | **Stakeholder surface** | `GET /board`, `/board/view`, `/cards/*` | Which stores are recovering / need intervention / working well — and why not |

---

## Project Summary

This project extends the **Retail Campaign Automation** system by introducing a deterministic **Retail Decision Intelligence** layer. It uses a **deterministic decision engine** to analyze store performance and generate explainable, evidence-based recommendations for retail decision-making while keeping humans in control of final approvals.

---

## Problem Statement

Project 1 identified the most effective recovery strategy for underperforming stores, while Project 2 automated campaign execution across eligible stores. However, retail managers still need to manually interpret campaign performance, monitor store recovery, and determine the next best action.

**Business Question**

> **How can retail managers receive accurate, explainable, and evidence-based recommendations by combining operational data, business rules, and retail knowledge into a single AI-powered decision intelligence system?**


## Current status

### Implemented

- Deterministic routing, planning, scoring, and verification over a composed, constructor-injected decision engine; every evaluation records its own trajectory into the recommendation.
- Shared forecast-service integration (`GET /stores`, `POST /predict`) and a typed actuals client (`GET /actuals/{store_id}`) that surfaces coverage gaps as evidence rather than backfilling them.
- Read-only campaign audit integration (local JSONL or an opt-in HTTP `/audit` endpoint), human-approval gates, and append-only JSONL recommendation + approval logging.
- Event-sourced Phase-2 intervention lifecycle (define → approve → start → complete → evaluate) with a durable, multi-worker-safe pending-approval store (SQLite).
- Outcome feedback loop: evaluated outcomes (observed sales + matched controls) feed back into the next round of recommendations.
- Grounded `/why` explanation endpoint: a deterministic template narrative with numeric + citation grounding; an LLM may rephrase it but only through the same guards, and is off by default.
- Pinned golden business scenarios plus cross-module consistency gates (`scripts/check.py`) running in CI before the test suite.

### Write authentication

State-mutating endpoints require a bearer token (`APPROVAL_AUTH_TOKEN`): `POST /approve/{store_id}`, `POST /reject/{store_id}`, `POST /recommendations/run`, `POST /monitor/sweep`, and every `POST /phase2/...` mutating endpoint (intervention define/events/checkpoints/outcome, plus portfolio evaluation). Send `Authorization: Bearer <token>`. Pending approvals are **durable and multi-worker-safe**: they live in a SQLite store (`app/state.py`, `PENDING_APPROVAL_STATE_PATH`) that is backfilled from the fsync'd append-only recommendation log on a fresh state file (decided stores are dropped, undecided approval-flagged stores are re-queued). If `APPROVAL_AUTH_TOKEN` isn't set, these endpoints refuse to serve (503) rather than silently allowing unauthenticated writes — a single shared token, not per-user accounts, so it is a floor above "anyone who can reach the port," not production-grade multi-user authorization. Read-only endpoints (`GET /recommendations`, `/board`, `/log`, `/pending-approvals`, `/attention-queue`) never mutate state. `GET /recommendations` returns the latest computed recommendations without evaluating or writing; use `POST /recommendations/run` to re-compute and persist. Repeat approve/reject calls for an already-decided store return the existing decision (200), not a 404, so retried requests don't misread as "this never happened."

## Local setup

Use Python 3.11 or newer. Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload --port 8001
```

`pip install -e ".[dev]"` installs the project itself (via `pyproject.toml`) plus test-only dependencies, so `pytest` runs directly with no `PYTHONPATH` juggling. Live-API integration tests are excluded by default (see `pytest.ini`); run them explicitly with `pytest -m live_api` once `CAMPAIGN_AUDIT_API_URL` is configured and the Forecast API is reachable.

`GET /health` reports service availability plus the sweep-scheduler status.

## Deployment (Docker)

```bash
# Build and run with the autonomous daily sweep enabled:
APPROVAL_AUTH_TOKEN=change-me docker compose up -d --build

# The append-only JSONL state (recommendations, approvals, phase2 events)
# lives on the `agent-logs` named volume; containers are disposable, history is not.
curl http://localhost:8001/health
```

CI (`.github/workflows/ci.yml`) runs on every push/PR: the offline test suite on Python 3.11 + 3.12, a cyclomatic-complexity gate (any D/E/F function fails the build), and a Docker build + container `/health` smoke test.

### Keep-alive for Render free tier

The deployed forecast/audit APIs on Render free tier spin down after ~15 min of idle, and a cold start takes 30–60s. Three complementary mitigations are shipped:

- `.github/workflows/keepalive.yml` — GitHub cron pings both services every 10 min (enable this; note GitHub pauses scheduled workflows after 60 days of repo inactivity, so for a hard guarantee also add the URLs to a free external pinger like UptimeRobot / cron-job.org at 10-min intervals).
- `scripts/keepalive.sh` — same ping logic to run from your own cron/`crontab -e`.
- `POST /monitor/sweep` warms the forecast API (`warm_up()`) *before* fanning out to stores, so a cold start is absorbed once instead of failing each store's first call; the typed clients already retry 503/502/504 with backoff.

## Project 2 integration

The agent consumes the `audit_log.jsonl` emitted by Retail Campaign Automation (Project 2) through a read-only adapter. It never imports Project 2 `main.py` and does not recreate campaign records or campaign logic. Configure the Project 2 audit file path before starting the service. If the file is absent, it reports no campaign data rather than manufacturing input.

As an opt-in alternative, set `CAMPAIGN_AUDIT_API_URL=https://retail-campaign-automation.onrender.com/audit`. It then makes only a `GET` request to that exact `/audit` endpoint; it never calls `/run-campaign`, `/advance-phase`, or `/rollback-phase`. The API must return `{"total_runs": <integer>, "runs": [<objects>]}` with a matching count. Each run must contain only non-empty `campaign` and `timing` text, a timezone-aware ISO-8601 `run_timestamp`, and unique positive integer `store_ids`. `campaign` is always retained verbatim as `campaign_label`. It additionally derives `campaign_id` from `campaign_label` when the label unambiguously encodes an integer (e.g. `Campaign 18`, `campaign-18`, `18`) — this pattern is verified against the underlying Dunnhumby `campaign_desc.csv`/`campaign_table.csv` ground truth, where campaign identity is always a plain integer with no duplicates or alternate naming. Records with a derived ID are flagged `campaign_provenance_status: NORMALIZED`, not treated as an originally-stable identifier; records whose label doesn't match this pattern (e.g. `Summer Promo`) get `campaign_id: null` and flag `MISSING_STABLE_CAMPAIGN_ID`. There is still no formal contract with Project 2 guaranteeing this labeling convention holds indefinitely, so no other module treats a `NORMALIZED` `campaign_id` as more authoritative than the `campaign_label` it was derived from. Rollout/action fields, including `ADVANCE_PHASE` and `rollout_status`, are outside this audit schema and cannot establish delivery success. HTTP, JSON, and schema failures are reported as campaign-audit integration errors; no records are invented.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `CAMPAIGN_AUDIT_LOG_PATH` | unset | Path to Project 2 `audit_log.jsonl`; unset/missing means no campaign data. |
| `CAMPAIGN_AUDIT_API_URL` | unset | Opt-in Project 2 read-only audit endpoint. When set, takes precedence over the local JSONL source. |
| `FORECAST_API_URL` | `https://retail-forecast-api-7sue.onrender.com/` | Shared deployed forecast API base URL. |
| `RECOMMENDATION_LOG_PATH` | `logs/recommendation_log.jsonl` | Append-only recommendation JSONL location. |
| `PENDING_APPROVAL_STATE_PATH` | `logs/pending_approvals.db` | Durable SQLite file holding the pending-approval queue (shared across workers; backfilled from the recommendation log on a fresh file). |
| `SWEEP_ENABLED` | unset (off) | Opt-in background sweep scheduler: set `1` to run the recovery sweep automatically every `SWEEP_INTERVAL_SECONDS`. |
| `SWEEP_INTERVAL_SECONDS` | `86400` | Sweep interval when the scheduler is enabled. Failed ticks are logged and retried; the loop never dies. |
| `PORT` | `8001` | FastAPI listen port. |
| `APPROVAL_AUTH_TOKEN` | unset (fail-closed) | Bearer token for all state-mutating endpoints (`approve`/`reject`, `POST /recommendations/run`, `POST /monitor/sweep`, Phase-2 writes). Unset → these refuse to serve (503). |

Malformed JSONL lines in either log are skipped with a warning; valid lines remain readable and no source log is changed. Campaign timestamps must be ISO-8601 with a timezone; malformed or timezone-naive timestamps are ignored. Recommendations expose `forecast_status` as `AVAILABLE`, `NO_DATA`, or `ERROR`; technical details are logged but not exposed by the API. Forecast HTTP, network, and malformed-response failures are surfaced as technical failures and are not invented as business data.

## Operations and known boundaries

- **Log retention.** The append-only JSONL logs *are* the durable state and grow without bound. Deploy with external log rotation/retention (e.g. a daily `logrotate`/`log2ram` job) sized to your audit-retention requirement. The SQLite pending store (`PENDING_APPROVAL_STATE_PATH`) is derived state: safe to delete and rebuild from the recommendation log, but keep it on the persistent volume (`agent-logs`) so workers share it.
- **Auth is a shared token, not multi-user.** `APPROVAL_AUTH_TOKEN` is a single bearer token guarding every mutating endpoint. For per-user authorization, terminate TLS with an authenticating reverse proxy (or SSO) in front.
- **Calibration is the next milestone.** The health score and `confidence` are bounded, reviewer-recomputable heuristics, not fitted probabilities. The Phase-2 outcome + matched-control DiD feedback loop is implemented precisely so these thresholds can later be *calibrated and validated against observed outcomes*: evaluate offline, version the decision rules, and only promote a threshold change through the same golden-case gate (a recalibration changes the pinned cases in the same commit, with the rationale stated). Until then the heuristics should be read as "explainable business rules", not statistical estimates. Signed momentum is surfaced on every record (`recovery_direction`, `recovery_velocity`) so flat-vs-declining is distinguishable even though the bounded health score clamps negatives at 0 by design.
