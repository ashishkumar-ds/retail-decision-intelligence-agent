# Retail Decision Intelligence Series

> How three projects close the loop for underperforming retail stores:
> strategy decides what works, execution scales it, and decision
> intelligence decides what's next — deterministic, human-gated, and
> measured against matched controls.

**Series:** P1 Store Performance Analysis (DiD) → P2 Campaign Automation → P3 Decision Intelligence Agent  
**Data:** dunnhumby The Complete Journey — 2,595,732 transactions, 582 stores, 2,500 households, 30 campaigns  
**Stack:** LightGBM (Optuna) · FastAPI · n8n · Brevo

---

## Executive summary

Retail leaders know what to do, struggle to do it consistently, and
struggle more to know what to do next. This series solves the three gaps
as one closed loop:

1. **Strategy — decide what works.** P1 proves *Campaign 18 + Best
   Customers + 12PM-6PM* with causal validation, not correlation.
2. **Execution — automate it.** P2 scales that strategy across 85
   underperforming stores via phased automation, with a full audit trail.
3. **Intelligence — decide what's next.** P3 consumes execution evidence,
   live forecasts, and measured outcomes to recommend
   `CONTINUE / MONITOR / EXTEND / ESCALATE / PAUSE` — human-gated,
   explainable, evidence-driven.

> **The honest number.** A single-series counterfactual (+30.1%) is
> optimistic. Causal DiD (+2.84% ITT, −9.6% store-level, +9.7% market
> drift) is the scale-up truth. The system is built on the second.

All three services are production FastAPIs with typed, fail-closed
contracts. No LLM invents a fact; every recommendation cites a record.

## Why three projects

| Manual reality | Series answer |
|---|---|
| An analyst per store, inconsistent rules, no audit | Deterministic engine with centralized rules (`decision_engine/`), append-only recommendation log (`logs/recommendation_log.jsonl`) |
| Forecast-only uplift absorbs market drift | DiD `GET /controls` gates scale-up on `did >= 3%` (`CONFIRMED`, `decision_engine/causality.py`) |
| Campaign fires, then silence until the next review | `GET /actuals` replay → `POST /phase2/.../outcome` → the next `POST /recommendations/run` carries `outcome_evidence` |

```
P1 insights + forecast/actuals ─┐
                                ├─→ P3 Decision Intelligence ─→ human approval ─→ P2 intervention ─→ measured outcome ──┐
P2 audit (85 stores, 3 phases) ─┘                                                                  └─→ back to P3
```

---

## Strategy — P1 decides what works

**Location:** `projects/data-science-projects/dunnhumby-retail-performance-analysis/` ·
notebook `store_performance_analysis_with_DiD.ipynb` · API `api/app/main.py` (`:8002`)

**Data foundation.** `transaction_data.csv`, 2.59M rows, zero
nulls/duplicates; outliers dropped (`QUANTITY 89638, 85055, 61335`,
`SALES 840 / 631.8`, `DISC -180 / -130`) → 2,581,257 clean rows, 582
stores, DAY `1–711`, 30 campaigns, `Campaign 18 587–642 = 56d`.

**Finding.** RFM + timing + campaign ROI converge on **TypeA Campaign 18,
Best Customers, afternoon (12PM–6PM)**.

**Counterfactual (LightGBM, Optuna).** `store 299 52.9% wMAPE, 317 53.1%,
448 44.8%` → pooled `actual $12,446 vs forecast $9,569 = +30.1% [11.9,
51] p=0.001` (`api/app/main.py:40 VALIDATION`). Useful for operational
gating — not for scale-up.

**Causal validation (DiD).** A single series cannot separate the campaign
from market drift (`+9.7%` all-store drift `684,559 → 751,266`). DiD can:

- **Household ITT (primary):** `treated 1133, contaminated 1383 (overlap
  13–22), clean 1117, active 1123/981` → `pre 439.27→474.77 vs control
  121.32→144.36` → **ATT $12.46/HH/56d, +2.84% p=0.10, incremental
  $14,119** — small, fragile, parallel trends `p=0.897`.
- **Store DiD (Analysis B):** `Pareto >81% → 511 underperforming`,
  `≥80% coverage → 13 pool → k=10 NN on z-log pre-sales + intra-pre
  trend → 12 matched [288, 289, 293, 295, 297, 309, 339, 340, 341, 345,
  355, 31642]` → `treated −0.7% vs control +8.9% → DiD −9.6% [−23.6,
  +25.4] p=0.178`, `299 zero redemptions of 653 C18 (90 stores)`.

**Business implication.** Keep customer targeting; revise the scale-up
case. Power the next experiment for `~3%`, not `30%` — this `3.0%` is
`TARGET_UPLIFT_PCT` in `decision_engine/calibration.py`.

**API surface:**

| Endpoint | Contract |
|---|---|
| `GET /health` | `{"ok", stores_available: 109}` |
| `GET /stores` | `[{store_id, first_day, last_day, days_with_data}]` (109 after the ≥14d filter) |
| `POST /predict {store_id, day}` | `predicted_sales_value`, `days_forecasted_ahead` (iterative, 90d cap) |
| `GET /actuals/{id}?start_day&end_day` | Observed sales `[{day, date, sales_value}]`, gaps omitted, 400d cap — the feedback-loop arm |
| `GET /controls/{id}?pre_start&pre_end&post_start&post_end&k` | `k` NN z-scored, `causal: {did_uplift_pct, treated/control_change}`, `methodology: {matching, effect, caveat}` — guardrail input |

---

## Execution — P2 scales it

**Location:** `projects/retail-campaign-automation-with-n8n/` · `main.py` (`:8000`) ·
`datasets/stores.csv`, `datasets/customer demographic.csv`

**Tiering.** `filter_stores_by_phase()` sorts `total_customer` desc →
`Pilot 5 [31642, 317, 299, 289, 31582]`, `Phase 1 25`, `Phase 2 55` =
**85 stores**; `353 Best Customers` (`segment_cust == "Best Customers"`).

**Orchestration.** FastAPI business logic + n8n scheduled workflow +
Brevo `emailCampaigns` + Google Sheets/Gmail via n8n. `test_mode=true`
(default) skips Brevo — `household_key@campaign18.com` is flagged as a
known limitation.

**Gating.** `validate_campaign_benchmark()` on the pooled `+30.1%`
(env-override to the causal `2.84` via `PILOT_UPLIFT_PCT`, `main.py:190`,
for dunnhumby-grade deploys) + live `GET /stores` + `POST /predict`
7-day trend `get_forecast_signal()`. Decision: `ADVANCE / HOLD`.

**Audit.** `audit_log.jsonl`, append-only. `GET /audit?schema=contract`
returns the strict four fields `{campaign, timing, run_timestamp,
store_ids}` for P3 (`_AUDIT_CONTRACT_FIELDS`); `schema=full` keeps
rollout fields for humans and n8n. P3 is read-only.

---

## Intelligence — P3 decides what's next

**Location:** this repo (`:8001`) · `app/main.py`

**Deterministic core** (`decision_engine/`):

- `router.py` → `no_data / near_deadline (<14d) / standard`
- `planner.py` → executable steps, plan drives execution
- `scorer.py` → `health = 50*recovery/3.0 + 30*velocity/0.05 +
  20*completeness` (`calibration.py`), `confidence = 0.5 +
  nearest_boundary/40`; rules `≥70 CONTINUE`, `<40 +14d ESCALATE`,
  `<40 EXTEND`, `≤0 velocity +30d ESCALATE`, else `MONITOR`
- `verifier.py` → `VALID = {CONTINUE, MONITOR, EXTEND, ESCALATE,
  NEEDS_REVIEW, PAUSE, RETARGET, TIMING, REALLOCATE}` + duplicate guard
- `guardrails/` → human gate: `ESCALATE, EXTEND, NEEDS_REVIEW, PAUSE,
  RETARGET, TIMING, REALLOCATE` require `Bearer APPROVAL_AUTH_TOKEN`
  (fail-closed `503` if unset; `401/403` on bad token)

**Feedback loop** — outcome feedback, end to end:

1. `POST /recommendations/run` (Bearer) → pending approvals (concurrent
   `ThreadPool 8` + forecast `AVAILABLE / NO_DATA / ERROR`).
2. `POST /approve/{id}` (Bearer) → `POST /phase2/interventions/{store_id}`
   with the canonical intervention key (`PHASE_2_SPEC.md`) → `ACTIVE` guard.
3. `POST /phase2/.../events` — `start → ACTIVE`, `complete → COMPLETED`.
4. `POST /phase2/.../outcome {"started_day": 650, "as_of": "+61d",
   "auto_controls": true}` → `_observations_from_actuals` maps dataset
   days onto the intervention clock (`source="actuals_replay"`) →
   `phase2/evaluator.py` evaluates `56d baseline + 14d recent` with
   evidence `SUFFICIENT / PARTIAL / INSUFFICIENT / NOT_DUE /
   CONTRADICTORY` → matched-control DiD via `GET /controls`, persisted
   in the `evaluate` event.
5. The next `POST /recommendations/run` threads
   `outcome_evidence: {actual_uplift, target_assessment, causal: {did,
   assessment, scale_up_eligible}}` into the scorer — `MEETS_TARGET`
   boosts confidence only on `CONFIRMED`; evidence never flips a
   decision, only modulates it.

**Diversified policy** — only on `SUFFICIENT + MEETS_TARGET` plus explicit
context: `RETARGET` (≥50% non-Best households), `TIMING` (≥40% peak
concentration), `REALLOCATE` (budget-constrained + confidence ≥0.7) →
`phase2/budget_allocator.py` `score = lift*conf`, 25% cap water-filling,
$100 dust floor.

**Explainability.** `GET /why/{id}?question=` (`rag/explainer.py`):
Tier-1 citations `[rec:]`, `[event:]` + Tier-2 BM25 (`rag/retriever.py`)
over vetted methodology and data-dictionary sources. The numeric/citation
guard fails closed (`409`) — the endpoint refuses to answer rather than
serve an ungrounded narrative.

---

## Running the series

```bash
# 1. P1 :8002
cd projects/data-science-projects/dunnhumby-retail-performance-analysis/api
uvicorn app.main:app --port 8002 &

# 2. P2 :8000 (pointed at local P1)
cd projects/retail-campaign-automation-with-n8n
AUDIT_LOG_PATH=/tmp/p2_audit_log.jsonl FORECAST_API_URL=http://127.0.0.1:8002 \
  uvicorn main:app --port 8000 &
curl "http://127.0.0.1:8000/run-campaign?test_mode=true"   # Pilot 5
curl -X POST http://127.0.0.1:8000/advance-phase           # → Phase1 25 → Phase2 55

# 3. P3 :8001 (pointed at the P2 audit file + P1)
cd retail-decision-intelligence-agent
CAMPAIGN_AUDIT_LOG_PATH=/tmp/p2_audit_log.jsonl FORECAST_API_URL=http://127.0.0.1:8002 \
  APPROVAL_AUTH_TOKEN=testtoken uvicorn app.main:app --port 8001 &
curl -X POST -H "Authorization: Bearer testtoken" http://127.0.0.1:8001/recommendations/run
curl -X POST http://127.0.0.1:8001/approve/317 -H "Authorization: Bearer testtoken" -d '{"actor":"mgr"}'
curl -X POST http://127.0.0.1:8001/phase2/interventions/317 -d '{"intervention_key":{"store_id":317,"intervention_type":"recovery","target_segment":"Best Customers","campaign_variant":"Campaign 18","strategy_version":"v1"}}'
curl -X POST http://127.0.0.1:8001/phase2/interventions/<id>/events -d '{"event_type":"start"}'
curl -X POST http://127.0.0.1:8001/phase2/interventions/<id>/events -d '{"event_type":"complete"}'
# outcome uses dataset days, not wall clock
curl -X POST http://127.0.0.1:8001/phase2/interventions/<id>/outcome -d '{"started_day":650,"as_of":"2026-11-30T00:00:00Z","auto_controls":true}'
curl http://127.0.0.1:8001/why/317
```

**Live proof.** `317 started_day 650, 108 obs, baseline $92.63 → recent
$100.98 = +9.0% MEETS_TARGET`; matched controls `DiD +2.2% →
REVIEW_ZONE` → scale-up blocked (vs a high-lift store at `+36.7%
CONFIRMED`, eligible).

---

## Governance

- **Read-only contracts.** P3 never imports P2 code, never writes the
  audit log, never calls `/run-campaign`. `tools/campaign_tool.py`
  validates `total_runs`, ISO-8601 timezone-aware timestamps, and
  positive unique `store_ids`.
- **Human gate.** Seven actions are approval-required; bearer auth fails
  closed (`503` without a token); repeat decisions are idempotent (`200`,
  `app/main.py`).
- **Event-sourced lifecycle.** `phase2/registry.py` keeps an immutable
  `define → approve → start → complete → outcome_pending → evaluate`
  chain; replay is deterministic; `409` guards against repeated and
  conflicting transitions.
- **Causal honesty.** Forecast lift is labeled operational; scale-up is
  labeled causal (`CONFIRMED` at ≥3%, `causality.py`). The observational
  caveat is published in the `/controls` methodology payload.

---

## Positioning

Unlike dashboards and black-box agents, this series is a **provable
measurement chain**: every recommendation deterministic and reproducible,
every intervention event-sourced, every outcome measured against matched
controls with the methodology published, and every scale-up gated by
causal evidence under human approval.

What a dunnhumby/84.51 would recognize: customer-first science (P1 RFM,
P2 Best Customers), first-party transaction grounding (2.59M rows), and
unified loyalty/media/assortment thinking — compressed into one auditable
repo.

---

## Roadmap

- **Experiment engine.** Randomize the 85 stores (epsilon-greedy or
  Thompson sampling) powered for `~3%` effects — turning the DiD
  observational caveat into experimental proof.
- **Profit objective.** `score = (lift * basket * coverage - cost) * conf`
  in `phase2/budget_allocator.py`.
- **Continuous monitoring.** `POST /phase2/portfolio/evaluate` on a
  schedule + `GET /actuals` polling.
