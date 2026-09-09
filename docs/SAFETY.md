# Safety gates — canonical list

Every safety control in this service, where it runs, and how it fails. This is
the document an auditor reads first. Tier-1 evidence always wins over any
external or generated content (see `docs/rag_sources.md` for the tier contract).

## 1. Human approval gate (decision layer)
- **Where:** `guardrails/__init__.py` (`APPROVAL_REQUIRED_RECOMMENDATIONS`),
  enforced by `decision_engine/scorer.py` + `decision_engine/verifier.py`.
- **Rule:** ESCALATE, EXTEND_INTERVENTION, NEEDS_REVIEW, PAUSE_INTERVENTION,
  RETARGET_SEGMENT, TIMING_SHIFT, REALLOCATE_BUDGET require a human decision.
  Nothing executes on its own; recommendations only enter the pending queue.
- **Failure mode:** fail-closed — an ungated material action cannot be produced.

## 2. Decision-time double gate (approval layer)
- **Where:** `approvals/ledger.py::decision_gate`, wired into
  `POST /approve/{store_id}` and `POST /reject/{store_id}`.
- **Rule:** guardrail policy AND the deterministic verifier are re-run *at
  decision time*, not only at recommendation time (merchant-agent stage/apply
  discipline). Every decision is appended to the locked, append-only ledger
  (`logs/approval_ledger.jsonl`, `APPROVAL_LEDGER_PATH`) with the operator and
  the gate result.
- **Failure mode:** gate failure → HTTP 409, the decision is refused, the
  ledger is never written with an ungated approval. The pending queue entry is
  dropped so a stale record cannot be retried into approval.
- **Rebuild invariant:** pending state lives in a durable SQLite store
  (`app/state.py`, `PENDING_APPROVAL_STATE_PATH`), shared across workers; on a
  fresh state file it is backfilled at startup from the durable recommendation
  log (`app/main._rebuild_pending_approvals` → `seed_from`). The ledger is
  evidence of decisions, never the source of pending state. A
  the ledger is evidence of decisions, never the source of pending state. A
  decision written to the ledger is always accompanied by the decided record in
  the recommendation log (both writes happen inside the same request; the
  ledger write happens first and raises on gate failure).

## 3. Approval & write authentication
- **Where:** `app/main.py::_require_approval_auth` (approve/reject), and
  `app/main.py::_require_phase2_write_auth` (all Phase-2 state-mutating
  endpoints: intervention define/events/checkpoints/outcome, plus portfolio
  evaluation) and `POST /recommendations/run` / `POST /monitor/sweep`.
- **Rule:** Bearer token (`APPROVAL_AUTH_TOKEN`). Without a configured token all
  of these fail closed (503) rather than allowing unauthenticated decisions,
  intervention writes, or external forecast spend. Read-only endpoints
  (`GET /recommendations`, `/board`, `/log`, `/pending-approvals`) are
  unauthenticated and never mutate state.

## 4. Deterministic decision core
- **Where:** `decision_engine/` (router → planner → scorer → verifier).
- **Rule:** the same inputs always produce the same recommendation; confidence
  drops near decision boundaries; the LLM never changes, retunes, or extends a
  decision — it only rephrases a grounded explanation. Signed momentum
  (`recovery_direction`, `recovery_velocity`) is surfaced on every record so a
  declining store is distinguishable from a merely flat one (the bounded health
  *score* clamps negatives at 0 by design).

## 5. Grounding guards (explanation layer)
- **Where:** `rag/explainer.py` + `rag/llm_explainer.py`.
- **Rules:** every number in a narrative must be traceable to cited Tier-1
  evidence or Tier-2 corpus chunks (`numeric_grounding_check`); every citation
  must resolve (`validate_citations`); engine vocabulary is closed under a
  grounding lexicon (`lexicon_check`). The LLM path runs the SAME guards; any
  failure degrades to the deterministic template with a `guard.llm` status.

## 6. Provenance gates (boundary contracts)
- **Where:** `phase2/schemas.py` (Pydantic runtime validation of HTTP
  envelopes/request bodies), `tools/forecast_tool.py` / `tools/campaign_tool.py`
  (typed, fail-closed adapters).
- **Rule:** data crossing a process boundary is validated or rejected; missing
  coverage is a typed `DataLimitation`, never backfilled.

## 7. Feature switches
- **Where:** `app/config.py` (`RAG_ENABLED`, `PHASE2_ENABLED`,
  `ACTUALS_FEEDBACK_ENABLED`, `LLM_EXPLANATIONS_ENABLED` — LLM off by default).
- **Rule:** a disabled capability refuses to serve (503) naming its switch;
  `/health` reports flag state.

## 8. Persistence gates
- **Where:** `app/main.py::_evaluate_and_persist_stores` (batch verification
  gate — a failed batch check persists nothing), `memory/history.py` and
  `approvals/ledger.py` (append-only, flock + fsync JSONL).
- **Rule:** logs are never rewritten or deleted; malformed lines are skipped,
  warned, and preserved.

## 9. CI invariants (`scripts/check.py` + `evaluation/`)
- Cross-module drift gates (planner/steps, guardrails↔emittable set, schema↔
  read-model parity, state disjointness, derived artifacts, flow specs, ledger
  probes) and golden decision evals. Recalibration requires updating the golden
  cases in the same commit with a stated reason.
