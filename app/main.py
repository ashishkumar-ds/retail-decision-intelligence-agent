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
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse

from app.config import (
    actuals_feedback_enabled,
    llm_advisory_enabled,
    llm_explanations_enabled,
    phase2_enabled,
    rag_enabled,
)
from app.meta import VERSION
from app.monitor import is_campaign_working, rank_attention
from app.scheduler import (
    SWEEP_ENABLED_ENV,
    SweepScheduler,
    sweep_enabled,
    sweep_interval_seconds,
)
from app.state import PendingApprovalStore
from approvals.ledger import append_decision, decision_gate, read_decisions, utcnow_iso
from decision_engine.engine import DecisionEngine
from decision_engine.scorer import StoreSignal
from decision_engine.simulator import simulate_intervention
from decision_engine.verifier import verify_batch
from memory.history import append_log, read_log
from phase2.contracts import (
    COMPLETED,
    EVALUATED,
    FAILED,
    OUTCOME_PENDING,
    ApprovalRecord,
    CheckpointRecord,
    InterventionEvent,
    InterventionKey,
    InterventionRecord,
    InterventionSnapshot,
    OutcomeObservation,
    RecommendationRecord,
)
from phase2.evaluator import (
    BASELINE_DAYS,
    EVALUATION_WINDOW_DAYS,
    build_intervention_outcome_join,
    build_weekly_checkpoints,
    evaluate_outcome,
)
from phase2.portfolio import evaluate_store_portfolio
from phase2.registry import (
    InterventionRegistry,
    active_intervention_guard,
    exact_key_repetition_guard,
    resolve_project2_provenance,
)
from phase2.schemas import ActualsEnvelope, OutcomeRequestBody
from presentation.board import build_board, render_board_html
from presentation.cards import (
    build_approval_preview,
    build_attention_digest,
    build_recommendation_card,
)
from presentation.site import (
    render_approvals,
    render_dashboard,
    render_evals,
    render_page,
    render_simulate,
    render_why,
)
from rag.advisor import maybe_advisory_triage
from rag.corpus import load_corpus
from rag.explainer import explain_store
from tools.campaign_tool import (
    CampaignAuditResponseError,
    first_run_for_store,
    get_audit_log,
    get_store_ids_from_audit_log,
)
from tools.forecast_tool import (
    ForecastResponseError,
    get_actuals,
    get_control_comparison,
    get_evaluation_window_forecast,
    get_prediction,
    get_store_info,
    warm_up,
)

logger = logging.getLogger("retail_decision_agent")
logging.basicConfig(level=logging.INFO)

# Ensure the Tier-2 methodology corpus exists (deterministic rebuild from
# in-repo sources; cheap even when it already exists). Skipped entirely when
# the RAG feature switch is off - the /why endpoint refuses to serve anyway,
# so building the corpus would be wasted work on the startup path.
from rag.corpus import DEFAULT_CORPUS_PATH as _RAG_CORPUS_PATH  # noqa: E402
from rag.corpus import build_corpus as _build_rag_corpus  # noqa: E402

if rag_enabled() and not _RAG_CORPUS_PATH.exists():
    _build_rag_corpus()

RECOVERY_WINDOW_DAYS = 60
RUN_LOG_PATH = Path("logs/run_log.jsonl")
APPROVAL_AUTH_TOKEN_ENV = "APPROVAL_AUTH_TOKEN"

app = FastAPI(title="Retail Decision Intelligence Agent", version=VERSION)


def _rebuild_pending_approvals() -> dict[int, dict]:
    """Reconstruct the pending-approval queue from the durable log.

    The pending-approval store (``app/state.py``) is the durable fast-access
    view; the append-only log is the source of truth. Backfilling the store
    from the log at first start means a restart no longer loses
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


# Pending approvals live in a durable, multi-worker-safe SQLite store
# (app/state.py). The append-only recommendation log remains the system of
# record; the store is the derived, indexed pending-state cache keyed by
# store_id. On a fresh/empty state file we backfill it from the log so a
# restart never loses an undecided approval (the same rebuild invariant the
# old in-memory dict enforced, now durable and shared across workers).
_pending_approvals = PendingApprovalStore()
if not _pending_approvals:
    _pending_approvals.seed_from(_rebuild_pending_approvals())
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


def _verify_approval_token_value(token: str) -> str:
    """Token check for operator-site forms (same gate as the header dependency).

    Same fail-closed contract as ``_require_approval_auth``: no configured
    server-side token -> 503 (endpoints disabled); mismatch -> 403.
    """
    configured = os.getenv(APPROVAL_AUTH_TOKEN_ENV)
    if not configured:
        raise HTTPException(
            status_code=503,
            detail=f"Approval endpoints are disabled: set {APPROVAL_AUTH_TOKEN_ENV} to enable them.",
        )
    provided = (token or "").strip()
    if not secrets.compare_digest(provided, configured):
        raise HTTPException(status_code=403, detail="Invalid approval token.")
    return provided


def _require_phase2_write_auth(authorization: str | None = Header(default=None)) -> str:
    """Auth for Phase-2 state-mutating endpoints (writes, portfolio evaluation).

    Enforces the feature switch first (a disabled capability still refuses with
    its switch named - same contract as ``_require_phase2_enabled``), then the
    same bearer-token gate that protects approve/reject. Without this, anyone
    who could reach the port could inject intervention events or spend
    forecast-API budget via the portfolio/outcome handlers. Fails closed: if
    no server token is configured the endpoints refuse to serve (503).
    """
    _require_phase2_enabled()
    return _require_approval_auth(authorization)




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

    except (httpx.HTTPError, TimeoutError) as e:
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


_decision_engine = DecisionEngine()


def evaluate_store(store_id: int, audit_runs: list, outcome_evidence: dict | None = None) -> dict | None:
    signal = build_store_signal(store_id, audit_runs)
    if signal is None:
        return None

    # Pure decision core (decision_engine/engine.py): the pipeline runs inside
    # the engine and records its own trajectory into the record. This function
    # keeps the I/O: the adapters above and the flat timestamped run log here.
    rec = _decision_engine.evaluate(signal, outcome_evidence=outcome_evidence)
    for step in rec["trajectory"]["steps"]:
        log_run_step(store_id, step["step"], step["status"], step["detail"])

    return rec


def _require_phase2_enabled() -> None:
    """Refuse to serve the Phase-2 lifecycle API when the capability is off.

    Follows the enable_* stub pattern: a disabled capability changes no other
    behavior - it simply refuses (503) with the switch that turns it back on.
    """
    if not phase2_enabled():
        raise HTTPException(
            status_code=503,
            detail="Phase-2 intervention lifecycle is disabled: set PHASE2_ENABLED=true to enable it.",
        )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "scheduler": _sweep_scheduler.status(),
        "features": {
            "rag": rag_enabled(),
            "phase2": phase2_enabled(),
            "actuals_feedback": actuals_feedback_enabled(),
            "llm_explanations": llm_explanations_enabled(),
            "llm_advisory": llm_advisory_enabled(),
        },
    }


def _fetch_audit_store_ids() -> list[int]:
    """Shared audit fetch for /recommendations and the sweep (HTTP errors -> 502/400)."""
    try:
        audit_runs = get_audit_log()
    except httpx.HTTPError as error:
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
    return audit_runs, all_store_ids


def _evaluate_and_persist_stores(audit_runs: list, store_ids: list[int]) -> tuple[list[dict], int, dict]:
    """Shared evaluate -> verify -> persist core for /recommendations and sweeps.

    Outcome evidence from evaluated Phase 2 interventions feeds back into
    scoring (plan -> execute -> measure -> re-decide). Verification gates
    persistence: a failed batch check never leaves partial state in the
    append-only log. Idempotent: unchanged recommendations keep their
    deterministic id; already-decided stores are not re-queued.
    """
    outcome_evidence_map = _outcome_evidence_by_store()
    with ThreadPoolExecutor(max_workers=8) as pool:
        evaluated = list(pool.map(
            lambda sid: evaluate_store(sid, audit_runs, outcome_evidence_map.get(sid)),
            store_ids,
        ))

    results = []
    for rec in evaluated:
        if rec is None:
            continue
        rec["recommendation_id"] = _recommendation_id(rec)
        results.append(rec)

    batch_check = verify_batch(results)
    if not batch_check["passed"]:
        logger.error("[VERIFICATION GATE] batch failed verification, nothing persisted: %s",
                     batch_check.get("failed_store_ids"))
        raise HTTPException(
            status_code=500,
            detail="Recommendation batch failed verification; nothing was persisted.",
        )

    log_records = read_log()
    existing_ids = {r.get("recommendation_id") for r in log_records}
    decided_store_ids = {r.get("store_id") for r in log_records if "decided_at" in r}
    newly_logged = 0
    for rec in results:
        if rec["recommendation_id"] not in existing_ids:
            # Audit symmetry: machine-generated records carry the system actor;
            # human approve/reject overwrite it with the operator identity.
            rec.setdefault("actor", "system:sweep")
            append_log(rec)
            newly_logged += 1
        if rec["requires_human_approval"] and rec["store_id"] not in decided_store_ids:
            _pending_approvals[rec["store_id"]] = rec
    return results, newly_logged, batch_check


@app.get("/recommendations")
def get_recommendations():
    """Read-only: the latest computed recommendation per store.

    No evaluation, no external forecast calls, no log writes, no queueing -
    a browser prefetch or a monitoring probe hitting this can no longer spend
    external budget or mutate state (the original side-effecting GET defect).
    To (re)compute and persist, use ``POST /recommendations/run`` (auth-gated).
    """
    latest: dict[int, dict] = {}
    for record in read_log():
        store_id = record.get("store_id")
        if isinstance(store_id, int):
            latest[store_id] = record
    return {"total_stores": len(latest), "read_only": True, "recommendations": list(latest.values())}


@app.post("/recommendations/run")
def run_recommendations(_auth: str = Depends(_require_approval_auth)):
    """Evaluate every store, persist new recommendations, and refresh the
    pending-approval queue. This is the side-effecting path that used to live
    on ``GET /recommendations``; it is now an explicit, authenticated write.
    """
    audit_runs, all_store_ids = _fetch_audit_store_ids()
    results, newly_logged, batch_check = _evaluate_and_persist_stores(audit_runs, all_store_ids)
    return {
        "total_stores_evaluated": len(results),
        "recommendations": results,
        "new_recommendations_logged": newly_logged,
        "batch_verification": batch_check,
    }


@app.get("/pending-approvals")
def get_pending_approvals():
    return {"count": len(_pending_approvals), "pending": list(_pending_approvals.values())}


@app.get("/attention-queue")
def get_attention_queue():
    """
    Ranked attention queue — best-practice store recovery view.

    Answers: should it get attention? Sorted by tier (must-act first) then
    urgency (low health, few days, low confidence, negative lift). Pure read
    over the pending-approval queue; add ?store_id= to filter.
    """
    ranked = rank_attention(list(_pending_approvals.values()))
    # Enrich each with is_campaign_working for the dashboard
    for rec in ranked:
        rec["campaign_working"] = is_campaign_working(rec)
    return {"count": len(ranked), "queue": ranked}


@app.post("/monitor/sweep")
def monitor_sweep(_auth: str = Depends(_require_approval_auth)):
    """
    Store Recovery Agent sweep — monitors is campaign working for all stores
    and refreshes the attention queue in one call.

    This is the autonomous loop entry point: it re-evaluates every store
    (same path as POST /recommendations/run) and returns the ranked queue.
    Idempotent; the scheduler calls the same core on its interval.
    """
    warm_up()  # absorb a Render cold start once, instead of in every store's first call
    audit_runs, all_store_ids = _fetch_audit_store_ids()
    results, newly_logged, batch_check = _evaluate_and_persist_stores(audit_runs, all_store_ids)
    ranked = rank_attention(list(_pending_approvals.values()))
    for rec in ranked:
        rec["campaign_working"] = is_campaign_working(rec)
    return {
        "swept_at": utcnow_iso(),
        "total_stores_evaluated": len(results),
        "new_recommendations_logged": newly_logged,
        "attention_queue_count": len(ranked),
        "attention_queue": ranked[:10],  # top 10 for brevity — full via GET /attention-queue
        "batch_verification": batch_check,
    }


def _scheduled_sweep() -> dict:
    """Scheduler tick: run the sweep and discard the response body.

    A tick that raises is recorded by the scheduler and retried next
    interval; nothing here needs the return value.
    """
    return monitor_sweep()


_sweep_scheduler = SweepScheduler(_scheduled_sweep, sweep_interval_seconds())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """App lifecycle (modern FastAPI idiom; replaces deprecated on_event hooks).

    Startup: start the opt-in sweep scheduler. Shutdown: stop it cleanly.
    """
    if sweep_enabled():
        _sweep_scheduler.start()
    else:
        logger.info("Sweep scheduler disabled (set %s=1 to enable)", SWEEP_ENABLED_ENV)
    yield
    _sweep_scheduler.stop()


app.router.lifespan_context = lifespan


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
    # Double gate (merchant-agent changes.py pattern): guardrails + verifier
    # re-run at decision time, not just at recommendation time. Fail closed.
    gate = decision_gate(rec)
    if not gate["allowed"]:
        _pending_approvals.pop(store_id, None)
        raise HTTPException(status_code=409, detail={
            "message": "Decision-time gate failed; approval refused.",
            "checks": gate["checks"],
        })
    append_decision(rec, "approve", actor, gate)
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
    # Double gate runs on rejection too: a rejected record is still a
    # decision over an approval-gated recommendation, so the same checks
    # must hold before it enters the audit ledger.
    gate = decision_gate(rec)
    if not gate["allowed"]:
        _pending_approvals.pop(store_id, None)
        raise HTTPException(status_code=409, detail={
            "message": "Decision-time gate failed; rejection not recorded.",
            "checks": gate["checks"],
        })
    append_decision(rec, "reject", actor, gate)
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
def create_phase2_intervention(store_id: int, payload: dict = Body(default={} ),
                               _auth: str = Depends(_require_phase2_write_auth)):
    """Register an intervention only from an existing human approval."""
    _require_phase2_enabled()
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
    _require_phase2_enabled()
    snapshot = _snapshot_or_404(intervention_id)
    reconstruction = _phase2_registry.reconstruct()
    return {"snapshot": jsonable_encoder(snapshot), "invalid_events": jsonable_encoder(reconstruction.invalid_events)}


@app.post("/phase2/interventions/{intervention_id}/events")
def append_phase2_lifecycle_event(intervention_id: str, payload: dict = Body(default={} ),
                                  _auth: str = Depends(_require_phase2_write_auth)):
    _require_phase2_enabled()
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
def record_phase2_checkpoints(intervention_id: str, payload: dict = Body(default={} ),
                              _auth: str = Depends(_require_phase2_write_auth)):
    _require_phase2_enabled()
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
    if not actuals_feedback_enabled():
        raise ValueError(
            "actuals replay feedback is disabled: set ACTUALS_FEEDBACK_ENABLED=true to enable it"
        )
    _validate_aware = started_at  # snapshot started_at is already validated
    envelope = ActualsEnvelope.model_validate(
        get_actuals(store_id, started_day - BASELINE_DAYS, started_day + EVALUATION_WINDOW_DAYS)
    )
    observations = tuple(
        OutcomeObservation(
            observed_at=started_at + timedelta(days=row.day - started_day),
            value=float(row.sales_value),
            metric_name="sales",
            source="actuals_replay",
            campaign_id=None,
            timing_window=None,
        )
        for row in envelope.observations
    )
    meta = {
        "started_day": started_day,
        "actuals_range_start_day": envelope.start_day,
        "actuals_range_end_day": envelope.end_day,
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
    except (httpx.HTTPError, ForecastResponseError, TypeError, ValueError) as error:
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


def _outcome_observations_from_payload(
    payload: dict, snapshot: InterventionSnapshot,
) -> tuple[tuple[OutcomeObservation, ...], dict | None]:
    """Resolve exactly one observation source: replay-mode actuals or
    client-posted observations. Mixing the two is rejected so evidence
    provenance stays unambiguous."""
    raw_observations = payload.get("observations", [])
    started_day = payload.get("started_day")
    if started_day is not None:
        # Replay mode: derive observations from observed sales actuals
        # served by the forecast API instead of client-posted values.
        if raw_observations:
            raise ValueError("provide either 'observations' or 'started_day', not both")
        if not isinstance(started_day, int) or isinstance(started_day, bool):
            raise ValueError("started_day must be an integer")
        if snapshot.started_at is None:
            raise ValueError("intervention has no valid start timestamp")
        return _observations_from_actuals(
            snapshot.key.store_id, started_day, snapshot.started_at,
        )
    observations = tuple(
        OutcomeObservation(
            observed_at=_parse_phase2_timestamp(item["observed_at"], "observed_at"), value=item["value"],
            metric_name=item.get("metric_name", "sales"), source=item.get("source", "project2"),
            campaign_id=item.get("campaign_id"), timing_window=item.get("timing_window"),
        ) for item in raw_observations
    )
    return observations, None


def _auto_forecast_reference(
    payload: dict, store_id: int, forecast_status: str | None,
) -> tuple[float | None, str | None]:
    """Opt-in forecast reference derivation. Returns (reference, status)."""
    forecast_reference_value = payload.get("forecast_reference_value")
    if forecast_reference_value is not None or not payload.get("auto_forecast", True):
        return forecast_reference_value, forecast_status
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
        elif forecast_status is None:
            forecast_status = "NO_DATA"
    except (httpx.HTTPError, ForecastResponseError, TimeoutError, TypeError, ValueError) as error:
        if forecast_status is None:
            forecast_status = "ERROR"
        forecast_reference_value = None
        logger.warning(f"Forecast API error for store {store_id}: {error}")
    return forecast_reference_value, forecast_status


@app.post("/phase2/interventions/{intervention_id}/outcome")
def evaluate_phase2_outcome(intervention_id: str, payload: dict = Body(default={} ),
                            _auth: str = Depends(_require_phase2_write_auth)):
    _require_phase2_enabled()
    try:
        # Pydantic gate on the request body: strict types for known fields,
        # unknown fields allowed (forward compatible). The original payload
        # dict remains the data source so evaluation semantics are unchanged.
        OutcomeRequestBody.model_validate(payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"invalid outcome input: {error}") from error
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
    auto_actuals_meta = None
    try:
        observations, auto_actuals_meta = _outcome_observations_from_payload(payload, snapshot)
        forecast_reference_value, forecast_status = _auto_forecast_reference(
            payload, snapshot.key.store_id, payload.get("forecast_status"),
        )
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
    if not rag_enabled():
        raise HTTPException(
            status_code=503,
            detail="RAG knowledge layer is disabled: set RAG_ENABLED=true to enable it.",
        )
    if question and len(question) > 500:
        raise HTTPException(status_code=400, detail="question must be at most 500 characters")
    try:
        result = explain_store(
            store_id,
            recommendation_records=read_log(),
            event_records=[e.to_record() for e in _phase2_registry.read_events()],
            corpus=load_corpus(),
            question=question,
            llm_enabled=llm_explanations_enabled(),
        )
    except ValueError as error:
        logger.error("[WHY ENDPOINT] grounding guard failed for store %s: %s", store_id, error)
        raise HTTPException(status_code=409, detail=str(error)) from error
    return jsonable_encoder(result)


@app.get("/advisory/{store_id}")
def advisory_triage(store_id: int, question: str = ""):
    """LLM triage suggestion for a human reviewer - ABOVE the human gate.

    Reads the SAME grounded evidence the /why endpoint serves, then (when
    LLM_ADVISORY_ENABLED) asks the configured LLM for a triage suggestion
    constrained to the engine's closed action vocabulary and grounded with
    the same numeric/citation guards. The suggestion is structurally inert:
    ``auto_applied`` is always False, ``requires_human_approval`` always
    True, and this endpoint writes nothing - no recommendation-log append,
    no ledger entry, no state mutation. The deterministic engine's own
    recommendation remains the only decision.
    """
    if not rag_enabled():
        raise HTTPException(
            status_code=503,
            detail="RAG knowledge layer is disabled: set RAG_ENABLED=true to enable it.",
        )
    if question and len(question) > 500:
        raise HTTPException(status_code=400, detail="question must be at most 500 characters")
    corpus = load_corpus()
    # Deterministic base: the TEMPLATE narrative (LLM off here), so the
    # advisory is grounded against the engine's own text, not an already-
    # rephrased one - no double-LLM drift.
    try:
        grounded = explain_store(
            store_id,
            recommendation_records=read_log(),
            event_records=[e.to_record() for e in _phase2_registry.read_events()],
            corpus=corpus,
            question=question,
            llm_enabled=False,
        )
    except ValueError as error:
        logger.error("[ADVISORY ENDPOINT] grounding guard failed for store %s: %s",
                     store_id, error)
        raise HTTPException(status_code=409, detail=str(error)) from error

    evidence = grounded["evidence"]
    latest = evidence.get("latest_recommendation") or {}
    current_rec = latest.get("recommendation", "NEEDS_REVIEW")
    fallback_note = latest.get("reason", "No grounded reason available; review manually.")

    # Same BM25 retrieval the /why tier-2 grounding uses, so the advisory
    # note is checked against the same allowed-number/citation universe.
    retrieved: list = []
    if corpus:
        base_query = str(current_rec)
        query = (question or base_query +
                 " difference-in-differences matched controls decision intelligence uplift")
        from rag.retriever import BM25Retriever
        retrieved = BM25Retriever(list(corpus)).retrieve(query, k=3)

    advisory, advisory_status = maybe_advisory_triage(
        store_id, question, current_rec, fallback_note,
        grounded["narrative"], evidence, corpus, retrieved,
        llm_enabled=llm_advisory_enabled(),
    )
    return jsonable_encoder({
        "store_id": store_id,
        "question": question,
        "engine_recommendation": current_rec,
        "narrative": grounded["narrative"],
        "citations": grounded["citations"],
        "advisory": advisory,
        "advisory_status": advisory_status,
        "invariants": {
            "auto_applied": False,
            "requires_human_approval": True,
            "writes_nothing": True,
        },
    })


@app.post("/phase2/portfolio/evaluate")
def evaluate_phase2_portfolio(payload: dict = Body(default={}),
                              _auth: str = Depends(_require_phase2_write_auth)):
    _require_phase2_enabled()
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


@app.get("/board")
def get_status_board():
    """Executive status board (the stakeholder surface).

    One deterministic view answering: which stores are recovering, which need
    intervention, where the campaign is working, and why it is not working
    where it is not. Pure classification over the persisted recommendation log;
    intervention entries are ordered by the monitor's attention ranking."""
    ranked = rank_attention(list(_pending_approvals.values()))
    for rec in ranked:
        rec["campaign_working"] = is_campaign_working(rec)
    board = build_board(
        read_log(),
        ranked_attention=ranked,
        pending_store_ids=set(_pending_approvals),
    )
    return jsonable_encoder(board)


@app.get("/board/view", response_class=HTMLResponse)
def get_status_board_html():
    """Human-readable board: same classification as GET /board, rendered HTML."""
    ranked = rank_attention(list(_pending_approvals.values()))
    board = build_board(
        read_log(),
        ranked_attention=ranked,
        pending_store_ids=set(_pending_approvals),
    )
    return render_board_html(board)


@app.get("/cards/pending")
def get_pending_cards():
    """`present_digest` analog: digest of pending approval-gated decisions.

    Every card field is enriched from the persisted recommendation log;
    cards carry evidence references, never inline claims."""
    pending = [_pending_approvals[sid] for sid in sorted(_pending_approvals)]
    return jsonable_encoder(build_attention_digest(pending))


@app.get("/cards/{store_id}")
def get_store_cards(store_id: int):
    """Decision summary + approval preview for one store's latest recommendation."""
    latest: dict | None = None
    for record in read_log():
        if record.get("store_id") == store_id:
            latest = record
    if latest is None:
        raise HTTPException(status_code=404, detail=f"No recommendation record for store {store_id}.")
    return jsonable_encoder({
        "recommendation_card": build_recommendation_card(latest),
        "approval_preview": build_approval_preview(latest),
    })


@app.get("/simulate/{store_id}")
def simulate_store_intervention(store_id: int, started_day: int):
    """Pre-approval backtest: replay the calibrated causal prior against the
    store's observed baseline (Priority 4 follow-on, market note sec. 3.4).

    Read-only compute - no state mutation, no auth. Combines the store's own
    baseline actuals with the Part 1 DiD calibration prior and returns the
    projected DiD band, the guardrail verdict that projection would earn, and
    the projected incremental sales value. Fail-closed: insufficient baseline
    coverage is INSUFFICIENT, never a invented projection.
    """
    if started_day <= BASELINE_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"started_day must exceed the baseline window ({BASELINE_DAYS} days)",
        )
    try:
        actuals = get_actuals(store_id, started_day - BASELINE_DAYS, started_day - 1)
    except (httpx.HTTPError, ForecastResponseError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=502,
            detail=f"Actuals service unavailable for simulation: {type(error).__name__}",
        ) from error
    return simulate_intervention(
        store_id,
        actuals.get("observations", []),
        started_day,
        pre_window_days=BASELINE_DAYS,
        evaluation_window_days=EVALUATION_WINDOW_DAYS,
    )


@app.get("/log")
def get_recommendation_log():

    log = read_log()
    return {
        "total_entries": len(log),
        "entries": log,
        # Decision-time double-gate audit trail (approvals/ledger.py): every
        # approve/reject with its decision-time gate checks.
        "decision_ledger": read_decisions(),
    }


# --- Operator site (server-rendered; the UI is a view, never a second source of truth) ---

@app.get("/ui", response_class=HTMLResponse)
def ui_dashboard():
    attention = rank_attention(list(_pending_approvals.values()))
    return render_page(
        "Decision dashboard", "/ui",
        render_dashboard(_pending_approvals.values(), read_log(), attention),
    )


@app.get("/ui/approvals", response_class=HTMLResponse)
def ui_approvals_page():
    return render_page("Approvals", "/ui/approvals",
                       render_approvals(list(_pending_approvals.values())))


async def _ui_decide_from_request(store_id: int, request: Request, approved: bool) -> HTMLResponse:
    from urllib.parse import parse_qs  # stdlib form parsing; no multipart dependency
    form = parse_qs((await request.body()).decode("utf-8"))
    return _ui_decide(store_id, form.get("token", [""])[0],
                      form.get("actor", [""])[0], approved)


def _ui_decide(store_id: int, token: str, actor: str, approved: bool) -> HTMLResponse:
    """Run the same double-gated decision the JSON API serves, from a form post."""
    route_fn = approve_recommendation if approved else reject_recommendation
    try:
        provided = _verify_approval_token_value(token)
        result = route_fn(store_id, payload={"actor": actor or None}, _auth=provided)
        message = str(result.get("message", "Recorded."))
    except HTTPException as error:
        message = f"{error.status_code}: {error.detail}"
    return HTMLResponse(render_page(
        "Approvals", "/ui/approvals",
        render_approvals(list(_pending_approvals.values()), banner=message),
    ))


@app.post("/ui/approve/{store_id}", response_class=HTMLResponse)
async def ui_approve(store_id: int, request: Request):
    return await _ui_decide_from_request(store_id, request, approved=True)


@app.post("/ui/reject/{store_id}", response_class=HTMLResponse)
async def ui_reject(store_id: int, request: Request):
    return await _ui_decide_from_request(store_id, request, approved=False)


@app.get("/ui/simulate", response_class=HTMLResponse)
def ui_simulate(store_id: int | None = None, started_day: int | None = None):
    if store_id is None or started_day is None:
        return render_page("Simulator", "/ui/simulate", render_simulate())
    if started_day <= BASELINE_DAYS:
        return render_page("Simulator", "/ui/simulate", render_simulate(
            store_id=store_id, started_day=started_day,
            error=f"started_day must exceed the baseline window ({BASELINE_DAYS} days)."))
    try:
        actuals = get_actuals(store_id, started_day - BASELINE_DAYS, started_day - 1)
        result = simulate_intervention(
            store_id, actuals.get("observations", []), started_day,
            pre_window_days=BASELINE_DAYS, evaluation_window_days=EVALUATION_WINDOW_DAYS,
        )
    except (httpx.HTTPError, ForecastResponseError, TypeError, ValueError) as error:
        return render_page("Simulator", "/ui/simulate", render_simulate(
            store_id=store_id, started_day=started_day,
            error=f"Simulation unavailable: {type(error).__name__} - {error}"))
    return render_page("Simulator", "/ui/simulate",
                       render_simulate(store_id=store_id, started_day=started_day, result=result))


@app.get("/ui/why/{store_id}", response_class=HTMLResponse)
def ui_why(store_id: int, question: str = ""):
    try:
        result = explain_recommendation(store_id, question=question)
    except HTTPException as error:
        return render_page(f"Why store {store_id}?", "/ui",
                           f"<div class='banner err'>{error.detail}</div>")
    return render_page(f"Why store {store_id}?", "/ui", render_why(store_id, result))


@app.get("/ui/evals", response_class=HTMLResponse)
def ui_evals():
    from evaluation.run_evals import EVAL_LOG_PATH
    records: list[dict] = []
    if EVAL_LOG_PATH.exists():
        for line in EVAL_LOG_PATH.read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue  # skip malformed audit lines, never crash the page
    return render_page("Eval history", "/ui/evals", render_evals(records))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
