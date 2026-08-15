"""
Retail Decision Intelligence Agent - Project 3

Orchestrates: route -> plan -> score -> verify -> approval_check, logging
every step (logs/run_log.jsonl) in addition to final recommendations
(logs/recommendation_log.jsonl). This file wires pieces together; it does
not contain decision logic itself.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException

from decision_engine.router import route
from decision_engine.planner import build_plan, describe_plan
from decision_engine.scorer import StoreSignal, score_and_recommend, no_data_recommendation
from decision_engine.verifier import verify_recommendation, verify_batch
from guardrails import requires_human_approval
from memory.history import append_log, read_log
from tools.campaign_tool import get_audit_log, get_store_ids_from_audit_log, first_run_for_store
from tools.forecast_tool import get_store_info, get_prediction

logger = logging.getLogger("retail_decision_agent")
logging.basicConfig(level=logging.INFO)

RECOVERY_WINDOW_DAYS = 60
RUN_LOG_PATH = Path("logs/run_log.jsonl")

app = FastAPI(title="Retail Decision Intelligence Agent", version="2.0.0")

# Pending approval state is intentionally separate from the durable log and is lost on restart.
_pending_approvals: dict[int, dict] = {}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_run_step(store_id: int, step: str, status: str, detail: str = "") -> None:
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG_PATH.open("a") as f:
        f.write(json.dumps({
            "store_id": store_id, "step": step, "status": status,
            "detail": detail, "timestamp": utcnow_iso(),
        }) + "\n")


def build_store_signal(store_id: int, audit_runs: list) -> StoreSignal | None:
    first_run = first_run_for_store(store_id, audit_runs)
    if first_run is None:
        return None

    first_run_date = datetime.fromisoformat(first_run["run_timestamp"])
    days_elapsed = (datetime.now(timezone.utc) - first_run_date).days
    days_remaining = max(RECOVERY_WINDOW_DAYS - days_elapsed, 0)

    try:
        store_info = get_store_info(store_id)
        if store_info is None:
            # Genuine business no-data, not a technical failure.
            log_run_step(store_id, "forecast_fetch", "no_data",
                         "Forecast API responded successfully but has no data for this store.")
            return StoreSignal(store_id, 0, 0, days_elapsed, days_remaining, False, "NO_DATA")

        baseline_day = store_info["last_day"]
        baseline = get_prediction(store_id, baseline_day)
        current = get_prediction(store_id, baseline_day + days_elapsed)

        log_run_step(store_id, "forecast_fetch", "success", f"baseline={baseline}, current={current}")
        return StoreSignal(store_id, baseline, current, days_elapsed, days_remaining, True, "AVAILABLE")

    except (requests.RequestException, TimeoutError) as e:
        # Technical failure (network/HTTP) - must not be treated as no-data.
        logger.error(f"[FORECAST INTEGRATION ERROR] store={store_id} network/HTTP failure: "
                     f"{type(e).__name__}: {e}")
        log_run_step(store_id, "forecast_fetch", "technical_error",
                     f"{type(e).__name__}: {e}")
        return StoreSignal(store_id, 0, 0, days_elapsed, days_remaining, False, "ERROR")

    except (KeyError, ValueError, TypeError) as e:
        # Technical failure (malformed response) - also not no-data.
        logger.error(f"[FORECAST INTEGRATION ERROR] store={store_id} malformed response: "
                     f"{type(e).__name__}: {e}")
        log_run_step(store_id, "forecast_fetch", "technical_error",
                     f"malformed response - {type(e).__name__}: {e}")
        return StoreSignal(store_id, 0, 0, days_elapsed, days_remaining, False, "ERROR")

    except Exception as e:
        # Last-resort catch: log loudly and continue, don't crash the batch.
        logger.error(f"[FORECAST INTEGRATION ERROR] store={store_id} unexpected failure: "
                     f"{type(e).__name__}: {e}", exc_info=True)
        log_run_step(store_id, "forecast_fetch", "unexpected_error",
                     f"{type(e).__name__}: {e}")
        return StoreSignal(store_id, 0, 0, days_elapsed, days_remaining, False, "ERROR")


def evaluate_store(store_id: int, audit_runs: list) -> dict | None:
    signal = build_store_signal(store_id, audit_runs)
    if signal is None:
        return None

    evaluation_route = route(signal)
    log_run_step(store_id, "route", "done", evaluation_route)

    plan = build_plan(evaluation_route)
    log_run_step(store_id, "plan", "done", "; ".join(describe_plan(plan)))

    # The plan drives execution, not just describes it.
    if "flag_for_review" in plan:
        rec = no_data_recommendation(signal)
        log_run_step(store_id, "score", "skipped", "no_data route - scoring skipped per plan")
    elif "score_and_recommend" in plan:
        rec = score_and_recommend(signal)
        log_run_step(store_id, "score", "done", rec["recommendation"])
    else:
        # Defensive fallback - should be unreachable, but never a silent no-op.
        rec = no_data_recommendation(signal)
        log_run_step(store_id, "score", "warning", f"unrecognized plan {plan} - defaulted to review")

    verification = verify_recommendation(rec)
    log_run_step(store_id, "verify", "done" if verification["passed"] else "warning", str(verification["details"]))

    # Guardrails is the single source of truth for approval requirements.
    rec["requires_human_approval"] = requires_human_approval(rec["recommendation"])
    rec["forecast_status"] = signal.forecast_status
    log_run_step(store_id, "approval_check", "done", f"requires_approval={rec['requires_human_approval']}")

    return rec


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/recommendations")
def get_recommendations():
    audit_runs = get_audit_log()
    all_store_ids = get_store_ids_from_audit_log(audit_runs)

    if not all_store_ids:
        raise HTTPException(
            status_code=400,
            detail="No store_ids found in the audit log. Requires Project 2's "
                   "run_campaign() to log 'store_ids' - see README.md.",
        )

    results = []
    for store_id in all_store_ids:
        rec = evaluate_store(store_id, audit_runs)
        if rec is None:
            continue
        results.append(rec)
        append_log(rec)
        if rec["requires_human_approval"]:
            _pending_approvals[store_id] = rec

    batch_check = verify_batch(results)

    return {
        "total_stores_evaluated": len(results),
        "recommendations": results,
        "batch_verification": batch_check,
    }


@app.get("/pending-approvals")
def get_pending_approvals():
    return {"count": len(_pending_approvals), "pending": list(_pending_approvals.values())}


@app.post("/approve/{store_id}")
def approve_recommendation(store_id: int):
    rec = _pending_approvals.pop(store_id, None)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No pending recommendation for store {store_id}.")
    rec = {**rec, "approved": True, "approved_at": utcnow_iso()}
    append_log(rec)
    return {"message": f"Recommendation for store {store_id} approved.", "recommendation": rec}


@app.post("/reject/{store_id}")
def reject_recommendation(store_id: int):
    rec = _pending_approvals.pop(store_id, None)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No pending recommendation for store {store_id}.")
    rec = {**rec, "approved": False, "rejected_at": utcnow_iso()}
    append_log(rec)
    return {"message": f"Recommendation for store {store_id} rejected.", "recommendation": rec}


@app.get("/log")
def get_recommendation_log():
    log = read_log()
    return {"total_entries": len(log), "entries": log}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
