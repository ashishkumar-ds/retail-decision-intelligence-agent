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
| Deterministic routing, planning, scoring, verification | IMPLEMENTED | Phase 1 decision engine |
| Forecast-service integration | IMPLEMENTED | Shared Render forecast API |
| Project 2 audit-log consumption | IMPLEMENTED | Read-only local JSONL adapter |
| Human approval gate | IMPLEMENTED | Process-local pending state |
| Recommendation history | IMPLEMENTED | Append-only JSONL |
| Intervention/outcome/evidence feedback loop | PLANNED | Phase 2 design and contract work |
| Adaptive policy/rule calibration | FUTURE | Requires outcome data and governance |
| RAG and curated retail knowledge retrieval | FUTURE | Not present in Phase 1 |
| LLM explanation or agentic orchestration | FUTURE | Not present in Phase 1 |
| Persistent approvals and production authentication | FUTURE | Explicit Phase 1 limitations |

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

`CONTINUE` and `MONITOR` do not require approval. Pending approval state is a
process-local dictionary and is intentionally separate from the durable
recommendation history. The approval endpoints are not authenticated in Phase
1; this is a known security limitation, not an implicit authorization model.

## 6. Recommendation persistence — IMPLEMENTED

Recommendations are appended to `RECOMMENDATION_LOG_PATH`, defaulting to
`logs/recommendation_log.jsonl`. Parent directories are created automatically.
Valid JSON object records survive graceful restarts. Malformed and non-object
lines are skipped with warnings when read. This is intentionally a simple
JSONL persistence mechanism, not a database, queue, event bus, or distributed
concurrency system.

## 7. Phase 2 intervention → outcome → evidence loop — PLANNED

Phase 2 should define the smallest traceable loop around the frozen Phase 1
recommendation:

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

## 8. Adaptive decision layer — FUTURE

After sufficient governed outcome history exists, an adaptive layer may propose
threshold or policy changes, compare intervention strategies, and quantify
uncertainty. Any adaptive proposal should be evaluated offline, versioned,
reviewed, and reversible before affecting deterministic production decisions.
The deterministic engine remains the executable safety boundary until explicit
governance approves a change.

Potential future capabilities include calibration against historical outcomes,
policy versioning, cohort analysis, uplift measurement, and controlled
experimentation. None is implemented in Phase 1.

## 9. Knowledge and LLM layers — FUTURE

The original Project 3 draft envisioned RAG, retail knowledge retrieval, LLM
explanations, and agentic tool orchestration. Those capabilities remain future
work. If introduced, they must be additive around the deterministic contract:

- retrieval may provide policy context, not unverified authority;
- an LLM may explain a deterministic result, not silently override it;
- tool calls must be allow-listed, observable, and scoped by read/write policy;
- generated text must cite the underlying recommendation and evidence; and
- human approval must remain mandatory for approval-required outcomes.

No vector database, LLM SDK, autonomous agent, or orchestration framework is
part of the approved Phase 1 system.

## 10. Limitations and security posture

Known intentional limitations are:

- approval state is lost on restart and is not shared across workers;
- approval/rejection endpoints have no authentication or approver identity;
- JSONL does not provide database-grade transactions or multi-writer guarantees;
- Campaign audit data is unavailable from the current Render root and must be
  supplied through the configured local file;
- forecast evaluation is synchronous and sequential per store;
- scoring and confidence are interpretable heuristics, not calibrated
  probabilities; and
- FastAPI runtime validation was skipped in the Android Python 3.14 environment
  because FastAPI/Pydantic could not be installed there.

These limitations should be treated as explicit boundaries, not hidden
assumptions or completed features.

## 11. Phase 2 handoff

Future Codex or LLM contributors should begin by reading this blueprint and
`docs/PHASE_1_COMPLETION.md`. Before changing code, they should agree on the
Phase 2 intervention/outcome/evidence schemas and decide whether the Campaign
boundary remains file-based or gains a formally supported read-only API.

The next validation baseline is the frozen Phase 1 suite (`21 passed, 2
skipped`), the pinned dependency set, the live forecast contract
`predicted_sales_value`, and the prohibition on mutating Project 2 data. Any
future adaptive or LLM work must preserve deterministic fallback behavior,
guardrails, provenance, human control, and the explicit IMPLEMENTED/PLANNED/
FUTURE status distinction in this document.
