# From Strategy to Execution Automation to Decision Intelligence
## A Whitepaper on the Dunnhumby Retail Intelligence Series (P1 → P2 → P3)

**Series:** `P1 Store Performance Analysis (DiD)` → `P2 Campaign Automation with n8n` → `P3 Decision Intelligence Agent`  
**Stack:** dunnhumby The Complete Journey (2,595,732 transactions, 582 stores, 2,500 households, 30 campaigns) · LightGBM (Optuna) · FastAPI · n8n · Brevo  
**Author:** Retail Decision Intelligence Team — Public Release

---

### Executive Summary

Retail leaders know what to do, struggle to do it consistently, and struggle more to know what to do next. This series solves the three gaps as one closed loop:

1.  **Strategy — Decide what works:** P1 proves *Campaign 18 + Best Customers + 12PM-6PM* with causal validation, not correlation.
2.  **Execution — Automate it:** P2 scales that strategy across 85 underperforming stores via phased automation, with a full audit trail.
3.  **Intelligence — Decide what’s next:** P3 consumes execution evidence + live forecasts + measured outcomes to recommend `CONTINUE / MONITOR / EXTEND / ESCALATE / PAUSE` — human-gated, explainable, evidence-driven.

> **Single-series counterfactual (+30.1%) is optimistic. Causal DiD (+2.84% ITT, -9.6% store-level, +9.7% market drift) is the scale-up truth.** The system is built on the second.

All three services are production FastAPIs with typed, fail-closed contracts. No LLM invents a fact; every recommendation cites a record.

---

### 1. Why Three Projects

| Manual reality | Series answer |
|---|---|
| Analyst per store, inconsistent rules, no audit | Deterministic engine, centralized rules `decision_engine/`, append-only `logs/recommendation_log.jsonl` |
| Forecast-only uplift absorbs market drift | DiD `GET /controls` gates scale-up on `did>=3% (CONFIRMED)` `decision_engine/causality.py:31` |
| Campaign fires, then silence until next review | `GET /actuals` replay → `POST /phase2/.../outcome` → next `POST /recommendations/run` carries `outcome_evidence` |

```
P1 insights + forecast/actuals ─┐
                                ├─→ P3 Decision Intelligence ─→ human approval ─→ P2 intervention ─→ measured outcome ──┐
P2 audit (85 stores, 3 phases) ─┘                                                                  └─→ back to P3
```

---

### 2. Strategy — P1 Decides What Works

**Location:** `projects/data-science-projects/dunnhumby-retail-performance-analysis/` · Notebook `store_performance_analysis_with_DiD.ipynb` · API `api/app/main.py` (`:8002`, `v2.1.0`)

**Data foundation:** `transaction_data.csv` 2.59M rows, zero nulls/duplicates; outliers dropped `QUANTITY 89638,85055,61335`, `SALES 840/631.8`, `DISC -180/-130` → 2,581,257 clean, 582 stores, DAY `1-711`, 30 campaigns, `Campaign 18 587-642 =56d`.

**Finding:** RFM + timing + campaign ROI converge on **TypeA Campaign 18, Best Customers, afternoon (12PM-6PM)**.

**Counterfactual (LightGBM Optuna):** `store 299 52.9% wMAPE, 317 53.1%, 448 44.8%` → pooled `actual $12,446 vs forecast $9,569 = +30.1% [11.9,51] p=0.001` `api/app/main.py:40 VALIDATION`. Useful for operational gating.

**Causal validation (DiD, §8):** Single-series cannot separate campaign from market drift (`+9.7%` all-store drift `684,559→751,266`). DiD does:

*   **Household ITT (primary):** `treated 1133, contaminated 1383 (overlap 13,14,15,16,17,19,20,21,22), clean 1117, active 1123/981` → `pre 439.27→474.77 vs control 121.32→144.36` → **ATT $12.46/HH/56d, +2.84% p0.10, incremental $14,119** — small, fragile, parallel trends `p0.897`.
*   **Store DiD (Analysis B):** `Pareto >81% → 511 underperforming`, `≥80% coverage → 13 pool → k=10 NN on z-log pre-sales + intra-pre trend → 12 matched [288,289,293,295,297,309,339,340,341,345,355,31642]` → `treated -0.7% vs control +8.9% → DiD -9.6% [-23.6,+25.4] p0.178`, `299 zero` redemptions of `653` C18 (90 stores).

**Business implication:** Keep customer targeting, revise scale-up case. Power the next experiment for `~3%`, not `30%`. This `3.0%` becomes `decision_engine/calibration.py:9 TARGET_UPLIFT_PCT`.

**API surface (v2.1.0):**

| Endpoint | Contract |
|---|---|
| `GET /health` | `{"ok", stores_available:109}` |
| `GET /stores` | `[{store_id,first_day,last_day,days_with_data}]` (109 after ≥14d filter) |
| `POST /predict {store_id,day}` | `predicted_sales_value` `days_forecasted_ahead` (iterative `forecast_iteratively`, 90d cap) |
| `GET /actuals/{id}?start_day&end_day` | Observed sales `[{day,date,sales_value}]`, gaps omitted, `400d` cap — feedback loop arm |
| `GET /controls/{id}?pre_start&pre_end&post_start&post_end&k` | `k NN z-scored`, `causal:{did_uplift_pct,treated/control_change}`, `methodology:{matching,effect,caveat}` — guardrail input |

---

### 3. Execution Automation — P2 Scales It

**Location:** `projects/retail-campaign-automation-with-n8n/` · `main.py` (`:8000`) · `datasets/stores.csv` `datasets/customer demographic.csv`

**Tiering:** `filter_stores_by_phase()` sorts `total_customer` desc → `Pilot 5 [31642,317,299,289,31582]`, `Phase1 25`, `Phase2 55` = **85**; `353 Best Customers` (`segment_cust=="Best Customers"`).

**Orchestration:** FastAPI business logic + n8n scheduled workflow + Brevo `emailCampaigns` + Google Sheets/Gmail via `n8n`. `test_mode=true` (default) skips Brevo — `household_key@campaign18.com` is flagged `KNOWN LIMITATION`.

**Gating:** `validate_campaign_benchmark()` on pooled `+30.1%` (env-override to causal `2.84` via `PILOT_UPLIFT_PCT` `main.py:190` for dunnhumby-grade deploys) + live `GET /stores` + `POST /predict` 7-day trend `get_forecast_signal()`. Decision `ADVANCE/HOLD`.

**Audit:** `audit_log.jsonl` append-only. `GET /audit?schema=contract` returns strict 4 fields `{campaign,timing,run_timestamp,store_ids}` for P3 (`_AUDIT_CONTRACT_FIELDS`), `schema=full` keeps rollout fields for humans/n8n. P3 is read-only.

---

### 4. Decision Intelligence — P3 Decides What’s Next

**Location:** `retail-decision-intelligence-agent` (`:8001`) · `app/main.py`

**Deterministic core** `decision_engine/`:

*   `router.py:6` → `no_data / near_deadline (<14d) / standard`
*   `planner.py` → executable steps
*   `scorer.py:34` `health = 50*recovery/3.0 + 30*velocity/0.05 + 20*completeness` (`calibration.py:22-23`), `confidence 0.5+nearest/40`, rules `≥70 CONTINUE`, `<40 +14d ESCALATE`, `<40 EXTEND`, `≤0 velocity +30d ESCALATE`, else `MONITOR`
*   `verifier.py:5` `VALID={CONTINUE,MONITOR,EXTEND,ESCALATE,NEEDS_REVIEW,PAUSE,RETARGET,TIMING,REALLOCATE}` + duplicate guard; gates `logs/recommendation_log.jsonl`
*   `guardrails/__init__.py:3` human gate: `ESCALATE,EXTEND,NEEDS_REVIEW,PAUSE,RETARGET,TIMING,REALLOCATE` require `Bearer APPROVAL_AUTH_TOKEN` `app/main.py:111` (`503` if unset, `401/403`).

**Feedback loop (Improvement — outcome feedback loop):**

1. `POST /recommendations/run` (Bearer) → pending approvals (concurrent `ThreadPool 8` + forecast `AVAILABLE/NO_DATA/ERROR`).
2. `POST /approve/{id} Bearer` → ` POST /phase2/interventions/{store_id} {intervention_key: store_id,intervention_type,target_segment,campaign_variant,strategy_version}` (`PHASE_2_SPEC.md` canonical) → `ACTIVE` guard.
3. `POST /phase2/.../events start → ACTIVE, complete → COMPLETED`.
4. `POST /phase2/.../outcome {"started_day":650,"as_of":"+61d",auto_controls:true}` → `_observations_from_actuals` maps `DAY→started_at+(D-started_day)` `source="actuals_replay"` `app/main.py:610` → `phase2/evaluator.py:76` `56d baseline +14d recent, evidence SUFFICIENT/PARTIAL/INSUFFICIENT/NOT_DUE/CONTRADICTORY` → `_causal_evidence_for_intervention` `GET /controls` persisted in `evaluate` event.
5. Next `POST /recommendations/run` threads `outcome_evidence:{actual_uplift, target_assessment, causal:{did, assessment, scale_up_eligible}}` into `scorer.py` — `MEETS_TARGET` only `CONFIRMED → +0.2`, else blocked/tempered; decisions never flipped, only modulated.

**Diversified policy (Priority 2) — only on `SUFFICIENT+MEETS_TARGET` + context:** `RETARGET ≥50% non-Best HH`, `TIMING ≥40% peak`, `REALLOCATE budget_constrained + conf≥0.7` → `phase2/budget_allocator.py:65` `score=lift*conf, 25% cap water-filling, $100 dust`.

**Explainability:** `GET /why/{id}?question=` `rag/explainer.py:87` Tier-1 citations `[rec:] [event:]` + Tier-2 BM25 `rag/retriever.py` over `rag/sources/methodology/{did_matched_controls,uplift_targeting,decision_intelligence}` + `data_dictionary` — `409` numeric/citation guard fail-closed, no LLM hallucination.

---

### 5. How to Run the Series (10 minutes)

```bash
# 1 P1 :8002
cd projects/data-science-projects/dunnhumby-retail-performance-analysis/api
uvicorn app.main:app --port 8002 &

# 2 P2 :8000 (pointed at local P1)
cd projects/retail-campaign-automation-with-n8n
AUDIT_LOG_PATH=/tmp/p2_audit_log.jsonl FORECAST_API_URL=http://127.0.0.1:8002 uvicorn main:app --port 8000 &
curl "http://127.0.0.1:8000/run-campaign?test_mode=true"  # Pilot 5
curl -X POST http://127.0.0.1:8000/advance-phase         # → Phase1 25 → Phase2 55

# 3 P3 :8001 (pointed at P2 file + P1)
cd retail-decision-intelligence-agent
CAMPAIGN_AUDIT_LOG_PATH=/tmp/p2_audit_log.jsonl FORECAST_API_URL=http://127.0.0.1:8002 APPROVAL_AUTH_TOKEN=testtoken uvicorn app.main:app --port 8001 &
curl -X POST -H "Authorization: Bearer testtoken" http://127.0.0.1:8001/recommendations/run
curl -X POST http://127.0.0.1:8001/approve/317 -H "Authorization: Bearer testtoken" -d '{"actor":"mgr"}'
curl -X POST http://127.0.0.1:8001/phase2/interventions/317 -d '{"intervention_key":{"store_id":317,"intervention_type":"recovery","target_segment":"Best Customers","campaign_variant":"Campaign 18","strategy_version":"v1"}}'
curl -X POST http://127.0.0.1:8001/phase2/interventions/<id>/events -d '{"event_type":"start"}'
curl -X POST http://127.0.0.1:8001/phase2/interventions/<id>/events -d '{"event_type":"complete"}'
# uses dataset days, not wall clock
curl -X POST http://127.0.0.1:8001/phase2/interventions/<id>/outcome -d '{"started_day":650,"as_of":"2026-11-30T00:00:00Z","auto_controls":true}'
curl http://127.0.0.1:8001/why/317
```

*Live proof:* `317 started_day 650 108 obs baseline $92.63→recent $100.98 = +9.0% MEETS_TARGET`, `controls did +2.2% REVIEW_ZONE → scale_up blocked` (vs `+36.7% CONFIRMED eligible` on a high-lift store).

---

### 6. Governance

*   **Read-only contracts:** P3 never imports P2 code, never writes `audit_log`, never guesses `/run-campaign`. `tools/campaign_tool.py:151` validates `total_runs`, `ISO8601 TZ`, `store_ids>0`.
*   **Human gate:** 7 actions approval-required, bearer fail-closed `503` if no token, idempotent `200 already decided` `app/main.py:373`.
*   **Event-sourced:** `phase2/registry.py` immutable `define→approve→start→complete→outcome_pending→evaluate`, replay deterministic, `409 REPEATED/ACTIVE` guard.
*   **Causal honesty:** Forecast lift labeled operational; scale-up labeled causal `causality.py:31 CONFIRMED ≥3%`. Observational caveat published in `/controls methodology`.

---

### 7. Positioning

> Unlike dashboards and black-box agents, this series is a **provable measurement chain**: every recommendation deterministic and reproducible, every intervention event-sourced, every outcome measured against matched controls with methodology published, every scale-up gated by causal evidence under human approval.

**What dunnhumby/84.51 would recognize:** Customer-first science (`P1` RFM + `P2` Best Customers), first-party transaction grounding (2.59M txns), unified loyalty/media/assortment thinking — here compressed to one auditable repo.

---

### 8. Next

*   **P5 Experiment engine:** Randomize 85 stores (`epsilon-greedy/Thompson`) powered for `~3%` effects — turns the DiD observational caveat into experimental proof.
*   **Profit objective:** `score = (lift * basket * coverage - cost) * conf` in `budget_allocator.py`.
*   **Continuous monitoring:** `POST /phase2/portfolio/evaluate` scheduled sweep + `GET /actuals` polling.

**Repo:** `retail-decision-intelligence-agent` (public: improvement section, `docs/README.md`; private: blueprint/audits/handoff).
