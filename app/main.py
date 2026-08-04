"""
Retail Decision Intelligence Agent - Project 3

Reads Project 2's audit log and forecast API (never raw CSVs - see
Blueprint v2 and the BRD's FR1). Runs the deterministic decision engine
from decision_engine.py. Persists its own recommendation log, separate
from Project 2's execution audit log (BRD FR6). Gates any
requires_human_approval recommendation behind an explicit approval
endpoint before it's considered actionable (BRD FR5) - this gate lives
here regardless of whether Project 2's own approval gate is present in
the deployed version, so Project 3 doesn't silently depend on Project 2
having a feature it may not currently have.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException

from app.decision_engine import StoreSignal, recommend

CAMPAIGN_API_URL = os.getenv("CAMPAIGN_API_URL", "https://retail-campaign-automation.onrender.com")
FORECAST_API_URL = os.getenv("FORECAST_API_URL", "https://retail-forecast-api-7sue.onrender.com")
RECOMMENDATION_LOG_PATH = Path(os.getenv("RECOMMENDATION_LOG_PATH", "recommendation_log.jsonl"))
RECOVERY_WINDOW_DAYS = 60  # the two-month target, in days

app = FastAPI(title="Retail Decision Intelligence Agent", version="1.0.0")

# In-memory pending-approval queue. Same durability caveat as Project 2's
# original _campaign_state: survives only as long as the process runs.
# Acceptable for now since every approval decision is also written to
# RECOMMENDATION_LOG_PATH - the queue is a working set, the log is the
# source of truth.
_pending_approvals: dict[int, dict] = {}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_recommendation_log(record: dict) -> None:
    with RECOMMENDATION_LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")


def read_recommendation_log() -> list[dict]:
    if not RECOMMENDATION_LOG_PATH.exists():
        return []
    with RECOMMENDATION_LOG_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def fetch_audit_log() -> list[dict]:
    resp = requests.get(f"{CAMPAIGN_API_URL}/audit", timeout=15)
    resp.raise_for_status()
    return resp.json().get("runs", [])


def first_run_for_store(store_id: int, audit_runs: list[dict]) -> dict | None:
    """
    Finds this store's earliest logged campaign run. Requires the audit
    log's 'store_ids' field (the one-line fix documented in
    DESIGN_SPEC.md) - without it, no run can be attributed to a specific
    store at all.
    """
    matches = [r for r in audit_runs if store_id in r.get("store_ids", [])]
    if not matches:
        return None
    return min(matches, key=lambda r: r["run_timestamp"])


def get_store_signal(store_id: int, audit_runs: list[dict]) -> StoreSignal | None:
    first_run = first_run_for_store(store_id, audit_runs)
    if first_run is None:
        return None

    first_run_date = datetime.fromisoformat(first_run["run_timestamp"])
    days_elapsed = (datetime.now(timezone.utc) - first_run_date).days
    days_remaining = max(RECOVERY_WINDOW_DAYS - days_elapsed, 0)

    try:
        stores_resp = requests.get(f"{FORECAST_API_URL}/stores", timeout=10)
        stores_resp.raise_for_status()
        store_info = next((s for s in stores_resp.json() if s["store_id"] == store_id), None)
        if store_info is None:
            return StoreSignal(store_id, 0, 0, days_elapsed, days_remaining, False)

        baseline_day = store_info["last_day"]
        baseline = requests.post(f"{FORECAST_API_URL}/predict",
                                  json={"store_id": store_id, "day": baseline_day}, timeout=10
                                  ).json()["predicted_sales_value"]
        current = requests.post(f"{FORECAST_API_URL}/predict",
                                 json={"store_id": store_id, "day": baseline_day + days_elapsed}, timeout=10
                                 ).json()["predicted_sales_value"]

        return StoreSignal(store_id, baseline, current, days_elapsed, days_remaining, True)

    except (requests.RequestException, KeyError, ValueError):
        return StoreSignal(store_id, 0, 0, days_elapsed, days_remaining, False)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/recommendations")
def get_recommendations():
    """
    Generates a fresh recommendation for every store that has ever
    appeared in Project 2's audit log. Read-only - nothing here executes
    or approves anything.
    """
    audit_runs = fetch_audit_log()
    all_store_ids = sorted({sid for r in audit_runs for sid in r.get("store_ids", [])})

    if not all_store_ids:
        raise HTTPException(
            status_code=400,
            detail="No store_ids found in the audit log. This requires Project 2's "
                   "run_campaign() to log 'store_ids' - see DESIGN_SPEC.md.",
        )

    results = []
    for store_id in all_store_ids:
        signal = get_store_signal(store_id, audit_runs)
        if signal is None:
            continue
        rec = recommend(signal)
        results.append(rec)
        append_recommendation_log(rec)
        if rec["requires_human_approval"]:
            _pending_approvals[store_id] = rec

    return {"total_stores_evaluated": len(results), "recommendations": results}


@app.get("/pending-approvals")
def get_pending_approvals():
    return {"count": len(_pending_approvals), "pending": list(_pending_approvals.values())}


@app.post("/approve/{store_id}")
def approve_recommendation(store_id: int):
    rec = _pending_approvals.pop(store_id, None)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No pending recommendation for store {store_id}.")

    rec = {**rec, "approved": True, "approved_at": utcnow_iso()}
    append_recommendation_log(rec)
    return {"message": f"Recommendation for store {store_id} approved.", "recommendation": rec}


@app.post("/reject/{store_id}")
def reject_recommendation(store_id: int):
    rec = _pending_approvals.pop(store_id, None)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No pending recommendation for store {store_id}.")

    rec = {**rec, "approved": False, "rejected_at": utcnow_iso()}
    append_recommendation_log(rec)
    return {"message": f"Recommendation for store {store_id} rejected.", "recommendation": rec}


@app.get("/log")
def get_log():
    log = read_recommendation_log()
    return {"total_entries": len(log), "entries": log}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
