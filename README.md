# Retail Decision Intelligence Agent

A deterministic decision-intelligence agent for retail store recovery. The
brain is code, not a model call: every recommendation is recomputable by
hand, citable by ID, and approved by a human before any budget moves.

[![CI](https://github.com/ashishkumar-ds/retail-decision-intelligence-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/ashishkumar-ds/retail-decision-intelligence-agent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230)](https://docs.astral.sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Overview

The agent closes the loop **plan → execute → measure → re-decide** for
underperforming stores. It consumes campaign-audit evidence and live sales
forecasts, scores every store with a transparent rule chain, proposes the
next best action, and gates anything that moves money behind a human
decision — with an event-sourced intervention lifecycle and grounded,
cited explanations for every call.

- **Deterministic by design** — the decision path is pure code with no LLM;
  identical evidence always produces an identical, auditable decision.
- **Human-gated by default** — budget-affecting actions (`EXTEND`,
  `PAUSE`, `REALLOCATE`, …) require an explicit approval; unauthenticated
  writes fail closed (503).
- **Grounded explanations** — `/why/{store_id}` answers with citations to
  the exact evidence records; the optional LLM layer may only rephrase and
  must pass the same numeric/citation guards.
- **Golden-case gated** — 22 pinned business scenarios plus cross-module
  consistency gates run in CI; a recalibration must change the pinned cases
  in the same commit with the rationale stated.

## Quickstart

Requires Python 3.10+.

```bash
git clone https://github.com/ashishkumar-ds/retail-decision-intelligence-agent.git
cd retail-decision-intelligence-agent

python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                          # offline suite; live-API tests are deselected by default
uvicorn app.main:app --port 8001
```

Optional quality gates (the same ones CI runs):

```bash
ruff check .                    # lint gate
python scripts/check.py         # cross-module consistency gate
python evaluation/run_evals.py  # 22 golden business scenarios
```

Run in Docker with the autonomous daily sweep enabled:

```bash
APPROVAL_AUTH_TOKEN=change-me docker compose up -d --build
curl http://localhost:8001/health
```

## How a decision is made

Every store evaluation runs a fixed pipeline — `route → plan → score →
verify → approval gate` — with each stage recorded in a `DecisionTrajectory`
embedded in the recommendation record itself.

![System architecture — deterministic, human-gated decision flow](docs/diagrams/architecture.png)
*The decision path is pure code with no LLM; the optional LLM layer sits off-path and may only rephrase grounded, cited explanations.*

| Component | Module | Responsibility |
| --- | --- | --- |
| Pipeline | `app/main.py` | FastAPI service; orchestrates the pipeline; sweep scheduler |
| Decision engine | `decision_engine/` | Pure, constructor-injected brain: routing, planning, scoring rules, verification |
| Memory | `memory/history.py` | Append-only JSONL recommendation log (fsync'd, locked, never rewritten) |
| Lifecycle | `phase2/` | Event-sourced intervention lifecycle: define → approve → start → complete → evaluate |
| Decision ledger | `approvals/ledger.py` | Double-gated approve/reject record; guardrails re-run at decision time |
| Guardrails | `guardrails/` | Approval-gate set, risk tiers, choice architecture, cost of inaction |
| Tools | `tools/` | Typed, fail-closed adapters to the forecast and campaign-audit services |
| Knowledge | `rag/` | Two-tier grounding: evidence IDs + deterministic BM25 over vetted sources |
| Evaluation | `evaluation/` | Golden business scenarios and calibration constants |
| Stakeholder surface | `presentation/` | Executive board, attention queue, recommendation cards |

## API surface

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/health` | GET | Liveness + sweep-scheduler status |
| `/recommendations` | GET | Latest recommendations (read-only) |
| `/recommendations/run` | POST | Recompute and persist (auth) |
| `/board`, `/board/view`, `/cards/*` | GET | Executive board and recommendation cards |
| `/why/{store_id}` | GET | Grounded, cited explanation |
| `/pending-approvals`, `/attention-queue` | GET | Approval queue and triage digest |
| `/approve/{store_id}`, `/reject/{store_id}` | POST | Human decision (auth) |
| `/phase2/interventions/*` | GET/POST | Intervention lifecycle: define, events, checkpoints, outcome (auth on writes) |
| `/phase2/portfolio/evaluate` | POST | Portfolio-level evaluation (auth) |
| `/monitor/sweep` | POST | On-demand recovery sweep (auth) |
| `/log` | GET | Append-only recommendation log |

All state-mutating endpoints require `Authorization: Bearer <token>` where
the token comes from `APPROVAL_AUTH_TOKEN`. If the token is unset, these
endpoints refuse to serve (503) rather than allow unauthenticated writes.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `APPROVAL_AUTH_TOKEN` | unset (fail-closed) | Bearer token for all state-mutating endpoints |
| `FORECAST_API_URL` | deployed forecast API | Shared forecast service base URL |
| `CAMPAIGN_AUDIT_LOG_PATH` | unset | Path to Project 2 `audit_log.jsonl` |
| `CAMPAIGN_AUDIT_API_URL` | unset | Opt-in read-only audit HTTP endpoint (takes precedence over the file) |
| `RECOMMENDATION_LOG_PATH` | `logs/recommendation_log.jsonl` | Append-only recommendation log location |
| `PENDING_APPROVAL_STATE_PATH` | `logs/pending_approvals.db` | Durable SQLite pending-approval queue (multi-worker safe) |
| `SWEEP_ENABLED` / `SWEEP_INTERVAL_SECONDS` | off / `86400` | Opt-in background sweep scheduler |
| `PORT` | `8001` | FastAPI listen port |

## Documentation

- [Whitepaper](docs/WHITEPAPER.md) — the P1 → P2 → P3 series and this agent's place in it
- [Project blueprint](docs/PROJECT_BLUEPRINT.md) — architecture record; capabilities marked implemented/planned
- [Safety](docs/SAFETY.md) — canonical list of every safety gate, its rule, and failure mode
- [Phase 2 spec](docs/PHASE_2_SPEC.md) — the intervention lifecycle contract
- [LLM integration patterns](docs/LLM_INTEGRATION_PATTERNS.md) — how the optional LLM layer is quarantined
- [Market benchmark](docs/market_benchmark_retail_di.md) — positioning against commercial retail-DI platforms

## Contributing

Keep the decision path deterministic: a change that alters a recommendation
must update the pinned golden cases in the same commit, with the rationale
stated. Run `ruff check .`, `pytest`, and `python scripts/check.py` before
opening a PR — CI runs all three plus a Docker build smoke test.

## License

MIT — see [LICENSE](LICENSE).
