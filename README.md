# Retail Decision Intelligence Agent

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
- Long-term memory, production authentication, and a persistent approval database.

## Local setup

Use Python 3.11 or newer. Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
uvicorn app.main:app --reload --port 8001
```

`GET /health` reports service availability. Approval items are held only in the process-local in-memory queue; recommendation history is separately persisted to JSONL and does survive restarts.

## Project 2 integration

Project 3 consumes the `audit_log.jsonl` emitted by Retail Campaign Automation (Project 2) through a read-only adapter. It never imports Project 2 `main.py` and does not recreate campaign records or campaign logic. Configure the Project 2 audit file path before starting Project 3. If the file is absent, Project 3 reports no campaign data rather than manufacturing input.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `CAMPAIGN_AUDIT_LOG_PATH` | unset | Path to Project 2 `audit_log.jsonl`; unset/missing means no campaign data. |
| `FORECAST_API_URL` | `https://retail-forecast-api-7sue.onrender.com/` | Shared deployed forecast API base URL. |
| `RECOMMENDATION_LOG_PATH` | `logs/recommendation_log.jsonl` | Append-only recommendation JSONL location. |
| `PORT` | `8001` | FastAPI listen port. |

Malformed JSONL lines in either log are skipped with a warning; valid lines remain readable and no source log is changed. Campaign timestamps must be ISO-8601 with a timezone; malformed or timezone-naive timestamps are ignored. Recommendations expose `forecast_status` as `AVAILABLE`, `NO_DATA`, or `ERROR`; technical details are logged but not exposed by the API. Forecast HTTP, network, and malformed-response failures are surfaced as technical failures and are not invented as business data.
