"""
Retail Decision Intelligence Agent - Project 3

Orchestrates: route -> plan -> score -> verify -> approval_check, logging
every step (logs/run_log.jsonl) in addition to final recommendations
(logs/recommendation_log.jsonl). This file wires pieces together; it does
not contain decision logic itself.
"""
import fcntl
import hashlib
import json
import logging
import os
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.encoders import jsonable_encoder

from decision_engine.router import route
from decision_engine.planner import build_plan, describe_plan
from decision_engine.scorer import StoreSignal, score_and_recommend, no_data_recommendation
from decision_engine.verifier import verify_recommendation, verify_batch
from guardrails import requires_human_approval
from memory.history import append_log, read_log
from rag.corpus import load_corpus
from rag.explainer import explain_store
from tools.campaign_tool import (
    CampaignAuditResponseError,
    get_audit_log,
    get_store_ids_from_audit_log,
    first_run_for_store,
)
from tools.forecast_tool import (
    ForecastResponseError,
    get_evaluation_window_forecast,
    get_actuals,
    get_control_comparison,
    get_prediction,
    get_store_info,
)
from phase2.contracts import (
    ACTIVE, APPROVED, CANCELLED, COMPLETED, EVALUATED, FAILED, OUTCOME_PENDING, PAUSED,
    CheckpointRecord, InterventionEvent, InterventionKey, InterventionRecord,
    ApprovalRecord, RecommendationRecord, OutcomeObservation,
)
from phase2.evaluator import (
    BASELINE_DAYS,
    EVALUATION_WINDOW_DAYS,
    build_intervention_outcome_join, build_weekly_checkpoints, evaluate_outcome,
)
from phase2.portfolio import evaluate_store_portfolio
from phase2.registry import (
    InterventionRegistry, active_intervention_guard, exact_key_repetition_guard,
    resolve_project2_provenance,
)

logger = logging.getLogger("retail_decision_agent")
logging.basicConfig(level=logging.INFO)

# Ensure the Tier-2 methodology corpus exists (deterministic rebuild from
# in-repo sources; cheap even when it already exists).
from rag.corpus import DEFAULT_CORPUS_PATH as _RAG_CORPUS_PATH, build_corpus as _build_rag_corpus
if not _RAG_CORPUS_PATH.exists():
    _build_rag_corpus()

RECOVERY_WINDOW_DAYS = 60
RUN_LOG_PATH = Path("logs/run_log.jsonl")
APPROVAL_AUTH_TOKEN_ENV = "APPROVAL_AUTH_TOKEN"

app = FastAPI(title="Retail Decision Intelligence Agent", version="2.0.0")


def _rebuild_pending_approvals() -> dict[int, dict]:
    """Reconstruct the pending-approval queue from the durable log.

    The in-memory queue is a fast-access view; the append-only log is the
    source of truth. Rebuilding at startup means a restart no longer loses
    pending approvals (recommendations flagged for approval and not yet
    decided are re-queued; decided stores are not).
    """
    pending: dict[int, dict] = {}
    for record in read_log():
        store_id = record.get("store_id")
        if not isinstance(store_id, int):
            continue
        if "decided_at" in record:
            pending.pop(store_id, None)
        elif record.get("requires_human_approval"):
            pending[store_id] = record
    return pending


_pending_approvals: dict[int, dict] = _rebuild_pending_approvals()
_phase2_registry = InterventionRegistry()


def _require_approval_auth(authorization: str | None = Header(default=None)) -> str:
    """Bearer-token guard for the approve/reject endpoints.

    Fails closed: if APPROVAL_AUTH_TOKEN isn't configured server-side, these
    endpoints refuse to serve at all rather than silently allowing
    unauthenticated approval/rejection of recommendations - consistent with
    this codebase's fail-closed philosophy elsewhere (never silently degrade
    a safety control just because it wasn't explicitly configured).
    """
    configured_token = os.getenv(APPROVAL_AUTH_TOKEN_ENV)
    if not configured_token:
        raise HTTPException(
            status_code=503,
            detail=f"Approval endpoints are disabled: set {APPROVAL_AUTH_TOKEN_ENV} to enable them.",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header. Expected 'Bearer <token>'.")
    provided_token = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(provided_token, configured_token):
        raise HTTPException(status_code=403, detail="Invalid approval token.")
    return provided_token



def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_run_step(store_id: int, step: str, status: str, detail: str = "") -> None:
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({
        "store_id": store_id, "step": step, "status": status,
        "detail": detail, "timestamp": utcnow_iso(),
    }) + "\n"
    with RUN_LOG_PATH.open("a") as f:
        # Exclusive lock so concurrent evaluation workers cannot interleave
        # partial JSON lines (same rationale as memory/history.append_log).
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _recommendation_id(rec: dict) -> str:
    """Deterministic id from decision content (wall-clock fields excluded).

    Same inputs -> same id, so re-running /recommendations with unchanged
    evidence does not grow the append-only log with duplicate records.
    """
    payload = "|".join(str(rec.get(k)) for k in (
        "store_id", "recommendation", "store_health_score",
        "recovery_pct", "days_remaining", "forecast_status",
    ))
    return f"recommendation-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def _outcome_evidence_by_store() -> dict[int, dict]:
    """Latest evaluated outcome per store from the Phase 2 event registry.

    The feedback loop input: evaluated outcomes (append-only ``evaluate``
    events carrying the full OutcomeEvaluation payload) are the only source
    of outcome evidence - it is never inferred or invented. Returns a map of
    store_id -> outcome payload dict (evidence_state, actual_uplift_pct,
    target_assessment, intervention_id).
    """
    evidence: dict[int, dict] = {}
    for event in _phase2_registry.read_events():
        if event.event_type != "evaluate" or event.key is None:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if not payload.get("evidence_state"):
            continue
        evidence[int(event.key.store_id)] = {
            "intervention_id": event.intervention_id,
            "evidence_state": payload.get("evidence_state"),
            "actual_uplift_pct": payload.get("actual_uplift_pct"),
            "target_assessment": payload.get("target_assessment"),
            "outcome_id": payload.get("outcome_id"),
            # Matched-control DiD evidence (Priority 3 causal guardrail), when
            # the outcome was evaluated with auto_controls enabled.
            **({"causal_evidence": payload["causal_evidence"]}
               if isinstance(payload.get("causal_evidence"), dict) else {}),
        }
    return evidence


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


def evaluate_store(store_id: int, audit_runs: list, outcome_evidence: dict | None = None) -> dict | None:
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
        causal_evidence = (outcome_evidence or {}).get("causal_evidence")
        rec = score_and_recommend(signal, outcome_evidence=outcome_evidence,
                                  causal_evidence=causal_evidence)
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
    try:
        audit_runs = get_audit_log()
    except requests.RequestException as error:
        logger.error("[CAMPAIGN AUDIT INTEGRATION ERROR] HTTP/network failure: %s", error)
        raise HTTPException(status_code=502, detail="Campaign audit API is unavailable.") from error
    except CampaignAuditResponseError as error:
        logger.error("[CAMPAIGN AUDIT INTEGRATION ERROR] invalid response: %s", error)
        raise HTTPException(status_code=502, detail="Campaign audit API returned an invalid response.") from error
    all_store_ids = get_store_ids_from_audit_log(audit_runs)

    if not all_store_ids:
        raise HTTPException(
            status_code=400,
            detail="No store_ids found in the audit log. Requires Project 2's "
                   "run_campaign() to log 'store_ids' - see README.md.",
        )

    # Evaluate stores concurrently - each store makes its own forecast API
    # calls, so sequential fan-out was an N+1 latency problem. Outcome
    # evidence from evaluated Phase 2 interventions feeds back into scoring:
    # measured lift modulates recommendation and confidence (the
    # plan -> execute -> measure -> re-decide loop).
    outcome_evidence_map = _outcome_evidence_by_store()
    with ThreadPoolExecutor(max_workers=8) as pool:
        evaluated = list(pool.map(
            lambda sid: evaluate_store(sid, audit_runs, outcome_evidence_map.get(sid)),
            all_store_ids,
        ))

    results = []
    for rec in evaluated:
        if rec is None:
            continue
        rec["recommendation_id"] = _recommendation_id(rec)
        results.append(rec)

    # Verification gates persistence: a failed batch check must never leave
    # partial state in the append-only log.
    batch_check = verify_batch(results)
    if not batch_check["passed"]:
        logger.error("[VERIFICATION GATE] batch failed verification, nothing persisted: %s",
                     batch_check.get("failed_store_ids"))
        raise HTTPException(
            status_code=500,
            detail="Recommendation batch failed verification; nothing was persisted.",
        )

    # Idempotent persistence: unchanged recommendations keep their
    # deterministic id and are not re-logged; already-decided stores are not
    # re-queued for approval.
    log_records = read_log()
    existing_ids = {r.get("recommendation_id") for r in log_records}
    decided_store_ids = {r.get("store_id") for r in log_records if "decided_at" in r}
    newly_logged = 0
    for rec in results:
        if rec["recommendation_id"] not in existing_ids:
            append_log(rec)
            newly_logged += 1
        if rec["requires_human_approval"] and rec["store_id"] not in decided_store_ids:
            _pending_approvals[rec["store_id"]] = rec

    return {
        "total_stores_evaluated": len(results),
        "recommendations": results,
        "new_recommendations_logged": newly_logged,
        "batch_verification": batch_check,
    }


@app.get("/pending-approvals")
def get_pending_approvals():
    return {"count": len(_pending_approvals), "pending": list(_pending_approvals.values())}


def _latest_decided_record(store_id: int) -> dict | None:
    """Most recent already-approved-or-rejected record for a store, if any.

    Used to make approve/reject idempotent: a repeat call for a store that
    was already decided (e.g. a retried request after a network blip, or two
    callers racing) returns the existing decision instead of a 404, since a
    404 on retry would incorrectly read as "this never happened."
    """
    for record in reversed(read_log()):
        if record.get("store_id") == store_id and "decided_at" in record:
            return record
    return None


@app.post("/approve/{store_id}")
def approve_recommendation(store_id: int, payload: dict = Body(default={}), _auth: str = Depends(_require_approval_auth)):
    actor = payload.get("actor") if isinstance(payload, dict) else None
    rec = _pending_approvals.pop(store_id, None)
    if rec is None:
        existing = _latest_decided_record(store_id)
        if existing is not None:
            return {"message": f"Recommendation for store {store_id} already decided.", "recommendation": existing}
        raise HTTPException(status_code=404, detail=f"No pending recommendation for store {store_id}.")
    decided_at = utcnow_iso()
    rec = {
        **rec,
        "approved": True,
        "approval_id": f"approval-{uuid.uuid4().hex}",
        "approved_at": decided_at,
        "decided_at": decided_at,
        "actor": actor,
    }
    append_log(rec)
    return {"message": f"Recommendation for store {store_id} approved.", "recommendation": rec}


@app.post("/reject/{store_id}")
def reject_recommendation(store_id: int, payload: dict = Body(default={}), _auth: str = Depends(_require_approval_auth)):
    actor = payload.get("actor") if isinstance(payload, dict) else None
    rec = _pending_approvals.pop(store_id, None)
    if rec is None:
        existing = _latest_decided_record(store_id)
        if existing is not None:
            return {"message": f"Recommendation for store {store_id} already decided.", "recommendation": existing}
        raise HTTPException(status_code=404, detail=f"No pending recommendation for store {store_id}.")
    decided_at = utcnow_iso()
    rec = {
        **rec,
        "approved": False,
        "approval_id": f"approval-{uuid.uuid4().hex}",
        "rejected_at": decided_at,
        "decided_at": decided_at,
        "actor": actor,
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


def _phase2_as_of_or_now(payload: dict) -> datetime:
    return _parse_phase2_timestamp(payload["as_of"], "as_of") if payload.get("as_of") else datetime.now(timezone.utc)


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
    as_of = _phase2_as_of_or_now(payload)
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


def _observations_from_actuals(
    store_id: int,
    started_day: int,
    started_at: datetime,
) -> tuple[tuple[OutcomeObservation, ...], dict]:
    """Build outcome observations from observed sales actuals (replay mode).

    Backtest convention, explicitly labeled: dataset DAY indexes are mapped
    onto the intervention-relative clock - the observation for dataset day D
    is observed at ``started_at + (D - started_day) days``. This keeps the
    evaluator's 56/14-day windows anchored to the real intervention start
    (required by the temporal join validation) while measuring genuinely
    observed sales, not forecasts. Source is tagged ``actuals_replay`` so
    downstream consumers can distinguish replayed actuals from live feeds.

    Days without transactions are absent from the actuals feed; coverage
    gaps flow through as evidence states (PARTIAL/INSUFFICIENT), never as
    invented values.
    """
    if isinstance(started_day, bool) or not isinstance(started_day, int):
        raise ValueError("started_day must be an integer")
    _validate_aware = started_at  # snapshot started_at is already validated
    envelope = get_actuals(store_id, started_day - BASELINE_DAYS, started_day + EVALUATION_WINDOW_DAYS)
    observations = tuple(
        OutcomeObservation(
            observed_at=started_at + timedelta(days=int(row["day"]) - started_day),
            value=float(row["sales_value"]),
            metric_name="sales",
            source="actuals_replay",
            campaign_id=None,
            timing_window=None,
        )
        for row in envelope.get("observations", [])
    )
    meta = {
        "started_day": started_day,
        "actuals_range_start_day": envelope.get("start_day"),
        "actuals_range_end_day": envelope.get("end_day"),
        "observation_count": len(observations),
    }
    return observations, meta


def _causal_evidence_for_intervention(store_id: int, started_day: int) -> dict:
    """Matched-control DiD evidence for an intervention (fail-open to INSUFFICIENT).

    Causal evidence is additive to the outcome, never a precondition for it:
    if the controls service is unavailable, malformed, or the store lacks
    coverage, we record WHY and the scorer's guardrail defaults to
    UNAVAILABLE (scale-up blocked) rather than failing the evaluation.
    """
    try:
        envelope = get_control_comparison(
            store_id=store_id,
            pre_start=started_day - BASELINE_DAYS,
            pre_end=started_day - 1,
            post_start=started_day,
            post_end=started_day + EVALUATION_WINDOW_DAYS,
        )
    except (requests.RequestException, ForecastResponseError, TypeError, ValueError) as error:
        logger.warning("[CAUSAL GUARDRAIL] controls fetch failed for store %s: %s: %s",
                       store_id, type(error).__name__, error)
        return {"evidence_state": "INSUFFICIENT",
                "reason": f"matched-control comparison unavailable ({type(error).__name__})"}
    causal = envelope.get("causal") or {}
    return {
        "evidence_state": "SUFFICIENT" if causal.get("did_uplift_pct") is not None else "INSUFFICIENT",
        "did_uplift_pct": causal.get("did_uplift_pct"),
        "control_store_ids": [c.get("store_id") for c in envelope.get("matched_controls", [])],
        "treated_change_pct": causal.get("treated_change_pct"),
        "control_change_pct": causal.get("control_change_pct"),
        "methodology": envelope.get("methodology"),
    }


@app.post("/phase2/interventions/{intervention_id}/outcome")
def evaluate_phase2_outcome(intervention_id: str, payload: dict = Body(default={} )):
    snapshot = _snapshot_or_404(intervention_id)
    if snapshot.lifecycle_state not in {COMPLETED, FAILED, OUTCOME_PENDING, EVALUATED}:
        raise HTTPException(status_code=409, detail="intervention lifecycle state is not eligible for outcome evaluation")
    if snapshot.lifecycle_state in {COMPLETED, FAILED}:
        pending = InterventionEvent(
            event_id=f"outcome-pending-{uuid.uuid4().hex}", intervention_id=intervention_id, event_type="outcome_pending",
            occurred_at=_phase2_as_of_or_now(payload), key=snapshot.key, campaign_id=snapshot.campaign_id,
            timing_window=snapshot.timing_window, outcome_id=payload.get("outcome_id"),
            payload={"execution_evidence": snapshot.execution_evidence} if snapshot.execution_evidence else {},
        )
        _phase2_registry.append_event(pending)
        snapshot = _snapshot_or_404(intervention_id)
    raw_observations = payload.get("observations", [])
    auto_actuals_meta = None
    try:
        started_day = payload.get("started_day")
        if started_day is not None:
            # Replay mode: derive observations from observed sales actuals
            # served by the forecast API instead of client-posted values.
            # Exactly one observation source is allowed - mixing posted and
            # fetched observations would make evidence provenance ambiguous.
            if raw_observations:
                raise ValueError("provide either 'observations' or 'started_day', not both")
            if not isinstance(started_day, int) or isinstance(started_day, bool):
                raise ValueError("started_day must be an integer")
            if snapshot.started_at is None:
                raise ValueError("intervention has no valid start timestamp")
            observations, auto_actuals_meta = _observations_from_actuals(
                snapshot.key.store_id, started_day, snapshot.started_at,
            )
        else:
            observations = tuple(
                OutcomeObservation(
                    observed_at=_parse_phase2_timestamp(item["observed_at"], "observed_at"), value=item["value"],
                    metric_name=item.get("metric_name", "sales"), source=item.get("source", "project2"),
                    campaign_id=item.get("campaign_id"), timing_window=item.get("timing_window"),
                ) for item in raw_observations
            )
        forecast_reference_value = payload.get("forecast_reference_value")
        forecast_status = payload.get("forecast_status")

        if forecast_reference_value is None and payload.get("auto_forecast", True):
            store_id = snapshot.key.store_id
            start_day = payload.get("start_day")
            try:
                if start_day is None:
                    store_info = get_store_info(store_id)
                    if store_info is not None:
                        start_day = store_info.get("last_day")
                if start_day is not None and isinstance(start_day, int):
                    forecast_reference_value = get_evaluation_window_forecast(
                        store_id=store_id,
                        start_day=start_day,
                        window_start_offset=47,
                        window_end_offset=60,
                    )
                    if forecast_status is None:
                        forecast_status = "AVAILABLE"
                else:
                    if forecast_status is None:
                        forecast_status = "NO_DATA"
            except (requests.RequestException, TimeoutError) as error:
                logger.warning(f"Forecast API network error for store {store_id}: {error}")
                if forecast_status is None:
                    forecast_status = "ERROR"
                forecast_reference_value = None
            except (ForecastResponseError, ValueError, TypeError) as error:
                logger.warning(f"Forecast API response/validation error for store {store_id}: {error}")
                if forecast_status is None:
                    forecast_status = "ERROR"
                forecast_reference_value = None

        calculation = evaluate_outcome(
            intervention_id=intervention_id, intervention_key=snapshot.key,
            intervention_started_at=snapshot.started_at, observations=observations,
            as_of=_parse_phase2_timestamp(payload["as_of"], "as_of") if payload.get("as_of") else None,
            outcome_id=payload.get("outcome_id"), forecast_reference_value=forecast_reference_value,
            forecast_status=forecast_status, campaign_id=snapshot.campaign_id,
            timing_window=snapshot.timing_window,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=f"invalid outcome input: {error}") from error
    join_inputs, join_error = _phase2_join_records(snapshot, calculation.outcome)
    if join_error is not None:
        join_state, join_reason = join_error
        return {"evidence_state": join_state, "reason": join_reason, "outcome": jsonable_encoder(calculation.outcome)}
    recommendation, approval, intervention, checkpoints, outcome = join_inputs
    join = build_intervention_outcome_join(recommendation, approval, intervention, checkpoints, outcome)
    # Causal guardrail evidence (Priority 3): opt-in via auto_controls, only in
    # replay mode where a real started_day anchors the pre/post windows.
    causal_evidence = None
    if payload.get("auto_controls") and isinstance(payload.get("started_day"), int):
        causal_evidence = _causal_evidence_for_intervention(
            snapshot.key.store_id, payload["started_day"],
        )
    if join.evidence_state == "SUFFICIENT" and snapshot.lifecycle_state != EVALUATED:
        evaluated = InterventionEvent(
            event_id=f"evaluate-{uuid.uuid4().hex}", intervention_id=intervention_id, event_type="evaluate",
            occurred_at=_phase2_as_of_or_now(payload), key=snapshot.key, campaign_id=snapshot.campaign_id,
            timing_window=snapshot.timing_window, outcome_id=calculation.outcome.outcome_id,
            payload={
                **jsonable_encoder(calculation.outcome),
                **({"causal_evidence": causal_evidence} if causal_evidence is not None else {}),
            },
        )
        _phase2_registry.append_event(evaluated)
    return {
        "evidence_state": join.evidence_state,
        "outcome": jsonable_encoder(calculation.outcome),
        "join": jsonable_encoder(join),
        **({"causal_evidence": causal_evidence} if causal_evidence is not None else {}),
        **({"actuals": auto_actuals_meta} if auto_actuals_meta is not None else {}),
    }


@app.get("/why/{store_id}")
def explain_recommendation(store_id: int, question: str = ""):
    """Grounded 'why' explanation for a store's recommendation (Priority 4).

    Tier-1 grounding: the narrative cites the store's recommendation record
    and Phase-2 registry events by ID. Tier-2 grounding: BM25-retrieved
    methodology chunks explain the reasoning principles. Fail-closed: the
    numeric grounding guard and citation guard refuse to serve an answer
    whose numbers or citations cannot be traced.
    """
    if question and len(question) > 500:
        raise HTTPException(status_code=400, detail="question must be at most 500 characters")
    try:
        result = explain_store(
            store_id,
            recommendation_records=read_log(),
            event_records=[e.to_record() for e in _phase2_registry.read_events()],
            corpus=load_corpus(),
            question=question,
        )
    except ValueError as error:
        logger.error("[WHY ENDPOINT] grounding guard failed for store %s: %s", store_id, error)
        raise HTTPException(status_code=409, detail=str(error)) from error
    return jsonable_encoder(result)


@app.post("/phase2/portfolio/evaluate")
def evaluate_phase2_portfolio(payload: dict = Body(default={})):
    store_ids = payload.get("store_ids", [])
    if not isinstance(store_ids, list) or not store_ids:
        raise HTTPException(status_code=400, detail="'store_ids' must be a non-empty list of integers")
    for sid in store_ids:
        if isinstance(sid, bool) or not isinstance(sid, int):
            raise HTTPException(status_code=400, detail="Each store_id must be an integer")

    report = evaluate_store_portfolio(
        store_ids=store_ids,
        transactions=payload.get("transactions", ()),
        campaign_households=payload.get("campaign_households", ()),
        campaign_start_day=int(payload.get("campaign_start_day", 587)),
        campaign_end_day=int(payload.get("campaign_end_day", 642)),
        auto_forecast=bool(payload.get("auto_forecast", True)),
    )
    return jsonable_encoder(report)


@app.get("/log")
def get_recommendation_log():

    log = read_log()
    return {"total_entries": len(log), "entries": log}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
