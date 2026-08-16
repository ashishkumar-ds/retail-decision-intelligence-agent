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
import uuid
from fastapi import Body, FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder

from decision_engine.router import route
from decision_engine.planner import build_plan, describe_plan
from decision_engine.scorer import StoreSignal, score_and_recommend, no_data_recommendation
from decision_engine.verifier import verify_recommendation, verify_batch
from guardrails import requires_human_approval
from memory.history import append_log, read_log
from tools.campaign_tool import get_audit_log, get_store_ids_from_audit_log, first_run_for_store
from tools.forecast_tool import get_store_info, get_prediction
from phase2.contracts import (
    ACTIVE, APPROVED, CANCELLED, COMPLETED, EVALUATED, FAILED, OUTCOME_PENDING, PAUSED,
    CheckpointRecord, InterventionEvent, InterventionKey, InterventionRecord,
    ApprovalRecord, RecommendationRecord, OutcomeObservation,
)
from phase2.evaluator import (
    build_intervention_outcome_join, build_weekly_checkpoints, evaluate_outcome,
)
from phase2.registry import (
    InterventionRegistry, active_intervention_guard, exact_key_repetition_guard,
    resolve_project2_provenance,
)

logger = logging.getLogger("retail_decision_agent")
logging.basicConfig(level=logging.INFO)

RECOVERY_WINDOW_DAYS = 60
RUN_LOG_PATH = Path("logs/run_log.jsonl")

app = FastAPI(title="Retail Decision Intelligence Agent", version="2.0.0")

# Pending approval state is intentionally separate from the durable log and is lost on restart.
_pending_approvals: dict[int, dict] = {}
_phase2_registry = InterventionRegistry()


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
        rec.setdefault("recommendation_id", f"recommendation-{uuid.uuid4().hex}")
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
    rec = {
        **rec,
        "approved": True,
        "approval_id": f"approval-{uuid.uuid4().hex}",
        "approved_at": utcnow_iso(),
        "decided_at": utcnow_iso(),
    }
    append_log(rec)
    return {"message": f"Recommendation for store {store_id} approved.", "recommendation": rec}


@app.post("/reject/{store_id}")
def reject_recommendation(store_id: int):
    rec = _pending_approvals.pop(store_id, None)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No pending recommendation for store {store_id}.")
    rec = {
        **rec,
        "approved": False,
        "approval_id": f"approval-{uuid.uuid4().hex}",
        "rejected_at": utcnow_iso(),
        "decided_at": utcnow_iso(),
    }
    append_log(rec)
    return {"message": f"Recommendation for store {store_id} rejected.", "recommendation": rec}



def _parse_phase2_timestamp(value, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field_name} must be a timezone-aware ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a timezone-aware ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HTTPException(status_code=400, detail=f"{field_name} must be timezone-aware")
    return parsed


def _phase2_key(payload: dict, store_id: int) -> InterventionKey:
    raw_key = payload.get("intervention_key")
    if not isinstance(raw_key, dict):
        raise HTTPException(status_code=400, detail="intervention_key is required; values are never guessed")
    try:
        key = InterventionKey.from_mapping(raw_key)
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=f"invalid intervention_key: {error}") from error
    if key.store_id != store_id:
        raise HTTPException(status_code=400, detail="intervention_key.store_id must match the path store_id")
    return key


def _latest_approved_record(store_id: int, recommendation_id: str | None = None) -> dict:
    records = read_log()
    for record in reversed(records):
        if record.get("store_id") != store_id or record.get("approved") is not True:
            continue
        if recommendation_id is not None and record.get("recommendation_id") != recommendation_id:
            continue
        if record.get("approval_id") and record.get("recommendation_id"):
            return record
    raise HTTPException(status_code=409, detail="no persisted approved recommendation is available")


def _snapshot_or_404(intervention_id: str):
    snapshot = _phase2_registry.reconstruct().snapshots.get(intervention_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Unknown Phase 2 intervention {intervention_id}")
    return snapshot


@app.post("/phase2/interventions/{store_id}")
def create_phase2_intervention(store_id: int, payload: dict = Body(default={} )):
    """Register an intervention only from an existing human approval."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be an object")
    approved = _latest_approved_record(store_id, payload.get("recommendation_id"))
    key = _phase2_key(payload, store_id)
    provenance = resolve_project2_provenance(store_id, get_audit_log())
    campaign_id = payload.get("campaign_id", provenance.get("campaign_id"))
    timing_window = payload.get("timing_window", provenance.get("timing_window"))
    if campaign_id is not None and not isinstance(campaign_id, str):
        raise HTTPException(status_code=400, detail="campaign_id must be a string or null")
    if timing_window is not None and not isinstance(timing_window, str):
        raise HTTPException(status_code=400, detail="timing_window must be a string or null")

    reconstruction = _phase2_registry.reconstruct()
    active_guard = active_intervention_guard(reconstruction.snapshots.values(), store_id)
    if active_guard["blocked"]:
        raise HTTPException(status_code=409, detail=active_guard)
    repetition_guard = exact_key_repetition_guard(reconstruction.snapshots.values(), key)
    if repetition_guard["blocked"]:
        raise HTTPException(status_code=409, detail=repetition_guard)

    intervention_id = f"intervention-{uuid.uuid4().hex}"
    approval_time = _parse_phase2_timestamp(approved.get("decided_at") or approved.get("approved_at"), "decided_at")
    definition = InterventionEvent(
        event_id=f"define-{uuid.uuid4().hex}", intervention_id=intervention_id, event_type="define",
        occurred_at=approval_time, key=key,
        campaign_id=campaign_id, timing_window=timing_window,
        recommendation_id=approved["recommendation_id"], actor=payload.get("actor"),
    )
    _phase2_registry.append_event(definition)
    approval_event = InterventionEvent(
        event_id=f"approve-{uuid.uuid4().hex}", intervention_id=intervention_id, event_type="approve",
        occurred_at=approval_time,
        key=key, campaign_id=campaign_id, timing_window=timing_window,
        approval_id=approved["approval_id"], recommendation_id=approved["recommendation_id"],
        actor=approved.get("approver"),
    )
    _phase2_registry.append_event(approval_event)
    snapshot = _phase2_registry.reconstruct().snapshots[intervention_id]
    return {"intervention_id": intervention_id, "lifecycle_state": snapshot.lifecycle_state, "key": key.canonical_dict()}


@app.get("/phase2/interventions/{intervention_id}")
def get_phase2_intervention(intervention_id: str):
    snapshot = _snapshot_or_404(intervention_id)
    reconstruction = _phase2_registry.reconstruct()
    return {"snapshot": jsonable_encoder(snapshot), "invalid_events": jsonable_encoder(reconstruction.invalid_events)}


@app.post("/phase2/interventions/{intervention_id}/events")
def append_phase2_lifecycle_event(intervention_id: str, payload: dict = Body(default={} )):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be an object")
    snapshot = _snapshot_or_404(intervention_id)
    event_type = payload.get("event_type")
    if event_type not in {"start", "pause", "resume", "complete", "fail", "cancel"}:
        raise HTTPException(status_code=400, detail="unsupported public lifecycle event")
    occurred_at = _parse_phase2_timestamp(payload.get("occurred_at", utcnow_iso()), "occurred_at")
    event = InterventionEvent(
        event_id=f"event-{uuid.uuid4().hex}", intervention_id=intervention_id, event_type=event_type,
        occurred_at=occurred_at, key=snapshot.key, campaign_id=snapshot.campaign_id,
        timing_window=snapshot.timing_window, recommendation_id=snapshot.recommendation_id,
        approval_id=snapshot.approval_id, actor=payload.get("actor"),
        payload=payload.get("payload", {}) if isinstance(payload.get("payload", {}), dict) else {},
    )
    _phase2_registry.append_event(event)
    updated = _snapshot_or_404(intervention_id)
    return {"snapshot": jsonable_encoder(updated), "invalid_events": jsonable_encoder(_phase2_registry.reconstruct().invalid_events)}


def _checkpoint_from_payload(raw: dict) -> CheckpointRecord:
    try:
        return CheckpointRecord(
            checkpoint_id=raw["checkpoint_id"], intervention_id=raw["intervention_id"],
            due_at=_parse_phase2_timestamp(raw["due_at"], "due_at"),
            observed_at=_parse_phase2_timestamp(raw["observed_at"], "observed_at") if raw.get("observed_at") else None,
            status=raw.get("status", "DUE"), metric_name=raw.get("metric_name", "sales"),
            metric_value=raw.get("metric_value"), source=raw.get("source", "project2"),
            campaign_id=raw.get("campaign_id"), timing_window=raw.get("timing_window"),
            intervention_key=InterventionKey.from_mapping(raw["intervention_key"]) if raw.get("intervention_key") else None,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=f"invalid checkpoint: {error}") from error


@app.post("/phase2/interventions/{intervention_id}/checkpoints")
def record_phase2_checkpoints(intervention_id: str, payload: dict = Body(default={} )):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be an object")
    snapshot = _snapshot_or_404(intervention_id)
    if snapshot.started_at is None:
        raise HTTPException(status_code=409, detail="intervention has no valid start timestamp")
    raw_observed = payload.get("observed_checkpoints", [])
    if not isinstance(raw_observed, list):
        raise HTTPException(status_code=400, detail="observed_checkpoints must be a list")
    observed = tuple(_checkpoint_from_payload(item) for item in raw_observed if isinstance(item, dict))
    as_of = _parse_phase2_timestamp(payload["as_of"], "as_of") if payload.get("as_of") else datetime.now(timezone.utc)
    checkpoints = build_weekly_checkpoints(
        intervention_id=intervention_id, intervention_started_at=snapshot.started_at,
        observed_checkpoints=observed, as_of=as_of, weeks=payload.get("weeks", 8),
    )
    for checkpoint in checkpoints:
        event_time = checkpoint.observed_at or checkpoint.due_at
        event = InterventionEvent(
            event_id=f"checkpoint-{uuid.uuid4().hex}", intervention_id=intervention_id, event_type="checkpoint",
            occurred_at=event_time, key=snapshot.key, campaign_id=checkpoint.campaign_id or snapshot.campaign_id,
            timing_window=checkpoint.timing_window or snapshot.timing_window, checkpoint_id=checkpoint.checkpoint_id,
            payload=jsonable_encoder(checkpoint),
        )
        _phase2_registry.append_event(event)
    return {"checkpoints": jsonable_encoder(checkpoints), "invalid_events": jsonable_encoder(_phase2_registry.reconstruct().invalid_events)}


def _phase2_join_records(snapshot, outcome):
    approved = _latest_approved_record(snapshot.key.store_id, snapshot.recommendation_id)
    try:
        recommendation = RecommendationRecord(
            recommendation_id=approved["recommendation_id"], store_id=snapshot.key.store_id,
            intervention_key=snapshot.key, recommendation=approved["recommendation"],
            generated_at=_parse_phase2_timestamp(approved["generated_at"], "generated_at"),
            reason=approved["reason"], campaign_id=snapshot.campaign_id, timing_window=snapshot.timing_window,
        )
        approval = ApprovalRecord(
            approval_id=approved["approval_id"], recommendation_id=approved["recommendation_id"],
            approved=True, decided_at=_parse_phase2_timestamp(approved["decided_at"], "decided_at"),
            approver=approved.get("approver"),
        )
        intervention = InterventionRecord(
            intervention_id=snapshot.intervention_id, approval_id=snapshot.approval_id,
            recommendation_id=snapshot.recommendation_id, intervention_key=snapshot.key,
            lifecycle_state=snapshot.lifecycle_state, started_at=snapshot.started_at, ended_at=snapshot.ended_at,
            campaign_id=snapshot.campaign_id, timing_window=snapshot.timing_window,
        )
    except (KeyError, TypeError, ValueError) as error:
        return None, ("INVALID", f"cannot reconstruct join records: {error}")

    checkpoints = []
    for event in _phase2_registry.read_events():
        if event.event_type != "checkpoint" or event.intervention_id != snapshot.intervention_id:
            continue
        try:
            checkpoints.append(_checkpoint_from_payload(event.payload))
        except HTTPException as error:
            return None, ("INVALID", error.detail)
    return (recommendation, approval, intervention, tuple(checkpoints), outcome), None


@app.post("/phase2/interventions/{intervention_id}/outcome")
def evaluate_phase2_outcome(intervention_id: str, payload: dict = Body(default={} )):
    snapshot = _snapshot_or_404(intervention_id)
    if snapshot.lifecycle_state not in {COMPLETED, FAILED, OUTCOME_PENDING, EVALUATED}:
        raise HTTPException(status_code=409, detail="intervention lifecycle state is not eligible for outcome evaluation")
    if snapshot.lifecycle_state in {COMPLETED, FAILED}:
        pending = InterventionEvent(
            event_id=f"outcome-pending-{uuid.uuid4().hex}", intervention_id=intervention_id, event_type="outcome_pending",
            occurred_at=datetime.now(timezone.utc), key=snapshot.key, campaign_id=snapshot.campaign_id,
            timing_window=snapshot.timing_window, outcome_id=payload.get("outcome_id"),
            payload={"execution_evidence": snapshot.execution_evidence} if snapshot.execution_evidence else {},
        )
        _phase2_registry.append_event(pending)
        snapshot = _snapshot_or_404(intervention_id)
    raw_observations = payload.get("observations", [])
    try:
        observations = tuple(
            OutcomeObservation(
                observed_at=_parse_phase2_timestamp(item["observed_at"], "observed_at"), value=item["value"],
                metric_name=item.get("metric_name", "sales"), source=item.get("source", "project2"),
                campaign_id=item.get("campaign_id"), timing_window=item.get("timing_window"),
            ) for item in raw_observations
        )
        calculation = evaluate_outcome(
            intervention_id=intervention_id, intervention_key=snapshot.key,
            intervention_started_at=snapshot.started_at, observations=observations,
            as_of=_parse_phase2_timestamp(payload["as_of"], "as_of") if payload.get("as_of") else None,
            outcome_id=payload.get("outcome_id"), forecast_reference_value=payload.get("forecast_reference_value"),
            forecast_status=payload.get("forecast_status"), campaign_id=snapshot.campaign_id,
            timing_window=snapshot.timing_window,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=f"invalid outcome input: {error}") from error
    if calculation.evidence_state == "SUFFICIENT" and snapshot.lifecycle_state != EVALUATED:
        evaluated = InterventionEvent(
            event_id=f"evaluate-{uuid.uuid4().hex}", intervention_id=intervention_id, event_type="evaluate",
            occurred_at=datetime.now(timezone.utc), key=snapshot.key, campaign_id=snapshot.campaign_id,
            timing_window=snapshot.timing_window, outcome_id=calculation.outcome.outcome_id,
            payload=jsonable_encoder(calculation.outcome),
        )
        _phase2_registry.append_event(evaluated)
        snapshot = _snapshot_or_404(intervention_id)
    join_inputs, join_error = _phase2_join_records(snapshot, calculation.outcome)
    if join_error is not None:
        join_state, join_reason = join_error
        return {"evidence_state": join_state, "reason": join_reason, "outcome": jsonable_encoder(calculation.outcome)}
    recommendation, approval, intervention, checkpoints, outcome = join_inputs
    join = build_intervention_outcome_join(recommendation, approval, intervention, checkpoints, outcome)
    return {
        "evidence_state": join.evidence_state,
        "outcome": jsonable_encoder(calculation.outcome),
        "join": jsonable_encoder(join),
    }


@app.get("/log")
def get_recommendation_log():
    log = read_log()
    return {"total_entries": len(log), "entries": log}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
