# Retail Decision Intelligence Agent — Project Blueprint

This blueprint is the forward-looking product and architecture record for
Project 3. It reconciles the original Project 3 draft vision with the approved
and frozen Phase 1 implementation. Every capability is explicitly marked
`IMPLEMENTED`, `PLANNED`, or `FUTURE` so that future contributors do not infer
that an aspirational component already exists.

## 1. Complete project vision

The Retail Decision Intelligence Agent is intended to help retail operators
answer a practical question: after a recovery or campaign intervention, what
should happen next for each store, and why?

The long-term system combines operational campaign data, forecast signals,
business rules, observed outcomes, and eventually curated retail knowledge to
produce explainable recommendations. Humans remain accountable for material
actions. Deterministic rules are the foundation and must remain inspectable
even when later adaptive or language-model capabilities are introduced.

### Status summary

| Capability | Status | Current boundary |
| --- | --- | --- |
| Deterministic routing, planning, scoring, verification | IMPLEMENTED | Phase 1 decision engine (`decision_engine/`: route → plan → score → verify → approval-check, pure, no I/O) |
| Forecast-service integration | IMPLEMENTED | Shared Render forecast API (`tools/forecast_tool.py`: typed adapter, retry/backoff, `AVAILABLE`/`NO_DATA`/`ERROR`; gaps are typed `DataLimitation`, never backfilled) |
| Project 2 audit-log consumption | IMPLEMENTED | Read-only JSONL + HTTP adapters (`tools/campaign_tool.py`); never imports P2 code, never writes audit records |
| Human approval gate | IMPLEMENTED | Central policy (`guardrails/`) + double gate at decision time (`approvals/ledger.py::decision_gate`) + durable SQLite pending store (`app/state.py`, Postgres via `DATABASE_URL`); bearer auth fail-closed 503 |
| Recommendation history | IMPLEMENTED | Append-only fsynced JSONL (`memory/history.py`); malformed lines skipped, never repaired |
| Intervention/outcome/evidence feedback loop | IMPLEMENTED | Phase 2 registry, checkpoints, outcome evaluator, evidence sufficiency, portfolio joins (`phase2/`); read-only over P2 |
| Execution (approved → acted) | IMPLEMENTED | Gated, idempotent, reversible journal (`execution/`); default connector is dry-run (no external write) — see §10 |
| Adaptive policy/rule calibration | IMPLEMENTED (report-only challenger) | V1 heuristic stays production; `decision_engine/policy_v2.py` tabular challenger + `phase2/budget_allocator.py` margin ranking + `evaluation/policy_eval.py` offline report. Promotion requires offline win + golden recalibration, same commit |
| RAG and curated retail knowledge retrieval | IMPLEMENTED (off-path) | Hand-rolled BM25 over in-repo corpus (`rag/`); retrieval is context, never authority; corpus rebuild pinned by `scripts/check.py` |
| LLM explanation / advisory | IMPLEMENTED (off-path, opt-in, fail-closed) | Template baseline + gated rephrase (`rag/explainer.py`, `rag/llm_explainer.py`); grounding/citation/lexicon vetoes, telemetry + alerts; `LLM_*_ENABLED` default off |
| Persistent approvals and approver identity | IMPLEMENTED (demo-grade auth) | SQLite default / Postgres via `DATABASE_URL` + versioned migrations (`storage/`); named tokens + roles (`approvals/identity.py`, `decided_by`). No OIDC/rotation — see §10 |

## 2. Project 1 → Project 2 → Project 3 relationship

The projects form a staged retail operating loop:

1. **Project 1 — recovery strategy** identifies an effective recovery approach
   for underperforming stores and establishes the business context for
   intervention.
2. **Project 2 — campaign automation** selects eligible stores and executes
   campaigns through its existing automation workflow. It writes execution
   records, including `audit_log.jsonl`.
3. **Project 3 — decision intelligence** consumes campaign execution evidence
   and forecast signals, evaluates store health, recommends the next action,
   and routes approval-sensitive recommendations to a human.

Project 3 does not import Project 2 code, duplicate campaign logic, or create
campaign records. Project 2 remains the system of record for campaign
execution; Project 3 is a read-only decision layer over that evidence.

## 3. Current Phase 1 architecture — IMPLEMENTED

```text
Project 2 audit_log.jsonl
        │ read-only CAMPAIGN_AUDIT_LOG_PATH adapter
        ▼
build_store_signal()
        │ forecast status + baseline/current forecast + elapsed window
        ▼
router → planner → scorer → verifier → guardrails
                                      │
                         human approval when required
                                      ▼
                         recommendation JSONL history
```

The Python package is `decision_engine/` (the former directory containing a
space was renamed). Its deterministic modules are:

- `router.py`: `no_data`, `near_deadline`, and `standard` paths.
- `planner.py`: executable step lookup for each route.
- `scorer.py`: recovery percentage, velocity, health score, confidence, and
  recommendation rules.
- `verifier.py`: recommendation validity, confidence, reason, approval-flag,
  and duplicate-store checks.

Supporting modules are `guardrails/`, `tools/campaign_tool.py`,
`tools/forecast_tool.py`, `memory/history.py`, and `app/main.py`.

## 4. Integration contracts — IMPLEMENTED

### Campaign audit contract

`CAMPAIGN_AUDIT_LOG_PATH` points to Project 2's append-only `audit_log.jsonl`.
The adapter reads JSON object lines and uses integer `store_ids` plus a
timezone-aware ISO-8601 `run_timestamp`. It ignores malformed lines and
malformed/naive timestamps with warnings, returns no data when the configured
file is absent, and never writes to the source.

The deployed Campaign root was smoke-tested with:

```text
GET https://retail-campaign-automation.onrender.com/
```

It returns service status metadata, not the audit records required by this
file-based contract. No Campaign API endpoint is guessed or integrated.

### Forecast contract

`FORECAST_API_URL` defaults to:
`https://retail-forecast-api-7sue.onrender.com/`.

The adapter uses:

```text
GET  /stores
POST /predict   {"store_id": <int>, "day": <int>}
```

`/stores` supplies store metadata including `store_id` and `last_day`.
`/predict` supplies the deployed numeric field `predicted_sales_value`.
Requests use a 10-second timeout. HTTP/network errors propagate as technical
failures; malformed JSON or nonnumeric fields raise a forecast response error.

Live validation consumed:

```text
store_id=27, day=642 → predicted_sales_value=54.9
```

### Forecast status semantics

Recommendations expose an operational status in addition to the deterministic
recommendation:

- `AVAILABLE`: metadata and both forecast values were obtained successfully.
- `NO_DATA`: the forecast service responded successfully but has no matching
  store data.
- `ERROR`: network, HTTP, timeout, malformed-response, or unexpected technical
  failure. Internal exception details are logged, not returned to API clients.

Both `NO_DATA` and `ERROR` preserve review-oriented recommendation behavior;
the status lets operators distinguish a business data gap from an integration
failure.

## 5. Guardrails and human control — IMPLEMENTED

The centralized approval policy requires human approval for:

- `ESCALATE`
- `EXTEND_INTERVENTION`
- `NEEDS_REVIEW`
- `PAUSE_INTERVENTION`
- `RETARGET_SEGMENT`, `TIMING_SHIFT`, `REALLOCATE_BUDGET` (diversified spend actions)

`CONTINUE` and `MONITOR` do not require approval. Pending approval state is a
durable SQLite store (`app/state.py`, `PENDING_APPROVAL_STATE_PATH`) derived from, and intentionally separate
from, the durable recommendation history, shared across workers (Postgres via
`DATABASE_URL` with versioned migrations in `storage/database.py`).
The approval, execute, and Phase-2 write endpoints require a bearer credential
(`APPROVAL_AUTH_TOKEN` legacy shared token, or `APPROVAL_TOKENS=token:user:role`
with `approver`/`viewer` roles in `approvals/identity.py`) and fail closed (503)
when unset; the authenticated principal is recorded as `decided_by` alongside
the caller-claimed `actor`. Every decision re-runs guardrails + verifier at
decision time (`approvals/ledger.py::decision_gate`). Risk tiers, default/fallback
pairs, and cost-of-inaction live in `guardrails/` (choice architecture).

## 6. Recommendation persistence — IMPLEMENTED

Recommendations are appended to `RECOMMENDATION_LOG_PATH`, defaulting to
`logs/recommendation_log.jsonl`. Parent directories are created automatically.
Valid JSON object records survive graceful restarts. Malformed and non-object
lines are skipped with warnings when read. This is intentionally a simple
JSONL persistence mechanism, not a database, queue, event bus, or distributed
concurrency system.

## 7. Phase 2 intervention → outcome → evidence loop — IMPLEMENTED

Phase 2 defines the smallest traceable loop around the frozen Phase 1
recommendation (see `docs/PHASE_2_SPEC.md` for the locked contract):

```text
recommendation
      ↓ human decision / approved intervention
intervention execution (Project 2 or an approved operator workflow)
      ↓ observed campaign and store outcomes
outcome measurement against baseline, forecast, and target window
      ↓ evidence record with provenance and time boundaries
decision review: continue, monitor, extend, escalate, or revise policy
```

The loop should preserve identity and provenance across recommendation,
approval, intervention, and outcome records. It should distinguish:

- what Project 3 recommended;
- what a human approved, rejected, or left pending;
- what Project 2 actually executed;
- what sales, forecast, uplift, confidence interval, and validation evidence
  was observed afterward; and
- which evidence supported the next recommendation.

Phase 2 should first specify schemas, correlation identifiers, time windows,
baseline definitions, missing-data behavior, delayed outcomes, and ownership of
each record. It must not silently turn an observed outcome into a new rule.

## 8. Adaptive decision layer — IMPLEMENTED (report-only challenger)

V1 (`decision_engine/scorer.py` heuristics) remains the production policy.
`decision_engine/policy_v2.py` is an offline tabular challenger (health tier ×
velocity sign × margin tier × availability; Laplace-style backoff to global
means at `MIN_CELL_N=3`); `phase2/budget_allocator.py` ranks spend by expected
incremental margin × confidence (money, not lift %); `decision_engine/simulator.py::compare_candidate_actions`
ranks CONTINUE / spend actions by `margin = base_total × lift/100 × margin_rate − cost`
with store-margin override (`tools/retail_context.py`) and reversibility risk.
`evaluation/policy_eval.py` replays V1-vs-V2 on held-out realised margins as a
CI report (never a gate). Promotion to production requires an offline win,
versioning, review, and golden-case recalibration in the same commit.

## 9. Knowledge and LLM layers — IMPLEMENTED (off-path, additive only)

RAG (`rag/corpus.py`, hand-rolled BM25 `rag/retriever.py`), the grounded
`GET /why/{store}` explainer (`rag/explainer.py`), the advisory triage draft
(`rag/advisor.py`), the retrieval pre-filter (`rag/prefilter.py`, fail-open),
root-cause tags (`analytics/root_cause.py`, egress redacted per ADR-0005), and
the LLM rephrase (`rag/llm_explainer.py`, `LLM_*_ENABLED` default off) are all
shipped strictly off-path around the deterministic contract:

- retrieval may provide policy context, not unverified authority;
- an LLM may explain a deterministic result, not silently override it;
- tool calls must be allow-listed, observable, and scoped by read/write policy;
- generated text must cite the underlying recommendation and evidence; and
- human approval must remain mandatory for approval-required outcomes.

No vector database, autonomous agent, or orchestration framework is
part of the system (deliberate: determinism over dependency; see
`docs/adr/0001-decision-path-is-pure-code.md`). Every off-path LLM attempt is
measured (`rag/llm_telemetry.py` → `logs/offpath_llm.jsonl`, `/metrics`
`retail_offpath_llm_*`, `ops/prometheus/alerts.yml`, `evaluation/llm_evals.py`
veto + provider-swap gate).

## 10. Limitations and security posture

Known intentional limitations are:

- execution default is dry-run: `execution/connector.py::DryRunConnector`
  records intent in the journal and performs no external write
  (`EXECUTION_CONNECTOR=dryrun` in `/health`; a real POS/CRM adapter implements
  `apply`/`reverse` and is injected at `POST /execute/{store_id}`);
- approval auth is demo-grade bearer tokens (`APPROVAL_AUTH_TOKEN` shared or
  `APPROVAL_TOKENS` map with `approver`/`viewer` roles): no rotation, expiry,
  or OIDC — do not publicly expose write paths without an additional control layer;
- JSONL is the audit system of record (append-only, flock + fsync); it does not
  provide database-grade transactions — the pending-approval *cache* is SQLite/Postgres;
- Campaign audit store universe requires `CAMPAIGN_AUDIT_API_URL` or
  `CAMPAIGN_AUDIT_LOG_PATH` (shipped `demo/` fallback); without either,
  `POST /recommendations/run` answers 400 and the sweep idles;
- forecast is a single upstream (`FORECAST_API_URL`): `ERROR` (integration
  failure) vs `NO_DATA` (business gap) are distinguished per record and surfaced
  on `/board`; there is no ensemble or last-good cache — a forecast outage
  leaves new recommendations in `NEEDS_REVIEW` until the feed recovers
  (see `docs/DEPLOYMENT.md` SLO);
- scoring and confidence are interpretable heuristics, not calibrated
  probabilities (`HEALTH_LOW/HIGH`, boundary confidence, diversification bars);
  the V2 challenger + `evaluation/policy_eval.py` report is the path to calibration,
  never a silent retune;
- simulator prior is global (Part 1 DiD `+2.84% CI [-0.5, 6.2]`): a gate, not a
  store ranking — realized effect is only knowable from matched-control DiD.

These limitations should be treated as explicit boundaries, not hidden
assumptions or completed features.

## 11. Phase 2 handoff

Future Codex or LLM contributors should begin by reading this blueprint.
Before changing code, they should agree on the
Phase 2 intervention/outcome/evidence schemas and decide whether the Campaign
boundary remains file-based or gains a formally supported read-only API.

The next validation baseline is the frozen Phase 1 suite (`21 passed, 2
skipped`), the pinned dependency set, the live forecast contract
`predicted_sales_value`, and the prohibition on mutating Project 2 data. Any
future adaptive or LLM work must preserve deterministic fallback behavior,
guardrails, provenance, human control, and the explicit IMPLEMENTED/PLANNED/
FUTURE status distinction in this document.
