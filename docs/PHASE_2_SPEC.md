# Phase 2 Specification — Governed Intervention Memory and Outcome Evaluation

## Status and scope

This is a documentation-only specification. It does not authorize changes to Phase 1 source code, tests, dependencies, APIs, Project 1, Project 2, or the Phase 1 architecture. Phase 1 deterministic decision logic is frozen.

Project 1 provides recovery strategy and context. Project 2 is the campaign execution system of record. Project 3 is the Retail Decision Intelligence Agent: a strictly read-only decision layer over Project 2 evidence. Phase 2 adds governed intervention memory, lifecycle evidence, checkpoints, and deterministic outcome evaluation around the existing Phase 1 engine.

Phase 2 does not introduce LLMs, RAG, adaptive ML, autonomous decision-making, synthetic campaign history, or autonomous intervention. Human approval remains required wherever Phase 1 requires it.

## 1. Design boundary and data flow

```text
Project 1 recovery context + Project 2 read-only execution evidence
                         + Phase 1 recommendation / approval records
                         ↓
intervention registry → immutable transitions → checkpoints
                         ↓
baseline + recent observation → deterministic outcome evaluation
                         ↓
evidence sufficiency → recommendation / intervention / outcome joins
```

Project 3 must not import Project 2 application code, duplicate campaign logic, write Project 2 records, call an undocumented Project 2 API, or manufacture missing campaign history. Forecast/reference values may support evaluation, but a forecast output alone can never establish intervention success. No evaluation may be described as causal campaign uplift or causal incremental lift.

## 2. Intervention Identity Reconciliation

The approved canonical Phase 2 intervention identity is:

```text
InterventionKey = {
    store_id: int,
    intervention_type: str,
    target_segment: str | null,
    campaign_variant: str | null,
    strategy_version: str
}
```

This five-field `InterventionKey` is the canonical identity of a governed intervention definition. It identifies the definition itself, not a recommendation, approval, execution run, checkpoint, or outcome.

Canonical-key rules:

1. `store_id` is required.
2. `intervention_type` is required.
3. `strategy_version` is required.
4. `target_segment` and `campaign_variant` may be null.
5. Missing and unknown values must not be silently guessed.
6. Serialization must use deterministic field ordering.
7. Canonical serialization must preserve the approved field order exactly as listed above.
8. String values are treated as opaque, schema-validated inputs; no unapproved semantic normalization or equivalence inference is permitted.
9. The canonical serialized key must be stable and reproducible.
10. `recommendation_id`, `approval_id`, `intervention_id`, `checkpoint_id`, and `outcome_id` remain separate immutable identifiers.

The original Project 3 identity remains a separate provenance concept:

```text
campaign_id + target_segment + timing_window
```

That original identity must not be discarded. `campaign_id` is an execution/campaign provenance reference, and `timing_window` is execution/timing metadata. `target_segment` may appear in both the canonical key and provenance records, but it does not by itself determine identity.

Relationship of identifiers and provenance:

- `InterventionKey` identifies the governed intervention definition.
- `campaign_id` identifies the execution or campaign provenance record when available.
- `timing_window` records execution timing metadata when available.
- `recommendation_id` identifies the recommendation artifact produced by Project 3.
- `approval_id` identifies the approval decision linked to that recommendation.
- `intervention_id` identifies the persisted intervention instance or registry entry.
- `checkpoint_id` identifies each checkpoint observation.
- `outcome_id` identifies the outcome record and its evaluation result.

The conflict between the canonical key and the original Project 3 provenance identity still matters for repeat detection, memory deduplication, attribution of execution evidence, and joins from recommendation to approval to intervention to checkpoint and outcome. The approved rule is that non-repetition semantics compare canonical `InterventionKey` values, not generated prose and not `campaign_id` alone. Additional business equivalence rules are not permitted unless explicitly approved later.

## 3. Lifecycle and persisted state

The registry is governed by immutable, persisted transition events. Process-local state is never authoritative. State is reconstructed by deterministic replay from the initial state using event timestamps and a stable tie-breaker; invalid events remain visible but are not applied. Corrections append superseding/versioned facts rather than rewriting history.

```text
RECOMMENDED ──approve──> APPROVED ──start──> ACTIVE
    │                       │                 ├──pause──> PAUSED
    ├──reject──> REJECTED   │                 ├──complete──> COMPLETED
    └──expire──> EXPIRED    └──cancel──> CANCELLED

COMPLETED ──> OUTCOME_PENDING ──> EVALUATED
FAILED    ──> OUTCOME_PENDING ──> EVALUATED  (only with valid execution evidence)
```

`REJECTED` and `EXPIRED` are terminal. `CANCELLED` is a terminal execution state; it enters outcome handling only when valid execution evidence exists. `PAUSED` is explicit and non-terminal, with explicit transitions to `ACTIVE` or `CANCELLED`. A paused intervention remains visible to the active guard. All transitions are timestamped, attributable to their event source or human action, and validated against the prior reconstructed state.

MVP implementation mechanics: lifecycle replay orders events by timestamp and uses original file order as the deterministic tie-break. Invalid events remain visible in diagnostics and are not applied.

## 4. Clocks and evaluation methodology

Store and intervention clocks remain separate. Persist timezone-aware ISO-8601 timestamps for approval, start, pause/resume, completion/failure, checkpoint due/observation, and outcome observation.

### Locked recovery evaluation window

The recovery evaluation window is 60 calendar days from a valid `intervention_started_at`. No evaluation window starts from recommendation, approval, forecast response, or an invalid execution timestamp.

### Locked baseline and observation

The baseline is the 56-day period immediately preceding intervention start, using the original Project 3 design’s baseline methodology exactly. Within the evaluation period, use the documented recent 14-day observation window, preserving its original aggregation, timestamps, and provenance.

### Locked target and recovery calculation

`TARGET_UPLIFT_PCT = 30.1%`. This preserves the original design’s target/reference context; it is not causal campaign uplift.

```text
actual_uplift_pct = (recent_observation - baseline) / baseline * 100
recovery_pct_of_target = actual_uplift_pct / 30.1 * 100
```

Any denominator, missing-data, invalid-input, clipping, or guard behavior already specified by the original Project 3 design must be preserved. Phase 2 must not add a new clipping rule or reinterpret a clipped value as causal lift. A zero/invalid baseline cannot produce a valid success result.

Forecast/reference values may be recorded as supporting evaluation evidence and comparison context. Forecast output alone never establishes intervention success, target attainment, or causal incremental lift.

Weekly checkpoints must preserve the original intervention-relative checkpoint convention. Each checkpoint records expected relative timing, due and observed timestamps, metric/value, source, provenance, and status. Checkpoint evidence supports monitoring and sufficiency; it is not by itself an outcome conclusion.
MVP implementation mechanics: checkpoints currently use a deterministic 7-day cadence while preserving the original intervention-relative convention.

## 5. Guards and evidence

Before approval/activation, the active-intervention guard inspects persisted reconstructed records for the same store. An intervention in `APPROVED`, `ACTIVE`, or `PAUSED` with no ended intervention clock blocks conflicting activation and returns deterministic `ACTIVE_INTERVENTION`. Missing data does not imply completion.

The non-repetition guard compares attempted interventions using the final approved canonical identity. It must block repetition according to the approved policy and must not infer equivalence beyond that decision. The policy horizon remains unresolved; no numeric horizon may be invented.

Checkpoint and outcome evidence must make missing, late, invalid, and contradictory facts visible. Supported outcome evidence states are `NOT_DUE`, `PARTIAL`, `SUFFICIENT`, `INSUFFICIENT`, `INVALID`, and `CONTRADICTORY`. Insufficient evidence has a deterministic review/monitor fallback and never becomes invented success, invented failure, an adaptive rule, or an autonomous intervention.

## 6. Outcome and evaluation joins

Independent IDs and provenance are required for recommendation, approval, intervention, checkpoint, and outcome records. Joins are:

```text
Recommendation → ApprovalDecision → Intervention
               → Checkpoints → Outcome → Evidence
```

Joins require matching store identity, the approved canonical intervention identity, compatible time ranges, and valid provenance. Missing, duplicate, late, or contradictory records remain visible in diagnostics rather than being dropped. `FAILED` reaches outcome evaluation only when valid execution evidence exists; otherwise it has no outcome window. `COMPLETED` and eligible `FAILED` records proceed through `OUTCOME_PENDING` and then `EVALUATED`.
MVP implementation mechanics: the 60-day evaluator uses arithmetic means over the locked 56-day baseline window and 14-day recent observation window. The join preserves provenance fields when they agree and rejects conflicting provenance deterministically using the existing evidence semantics (`INVALID` or `CONTRADICTORY`).

The evaluator exposes the 60-day due/observed timestamps, 56-day baseline, recent 14-day observation, calculation inputs, target/reference comparison, forecast provenance, evidence state, and evaluator/schema versions.

## 7. Unresolved policy decisions

The source material does not define the following. They must be approved before implementation; this specification assigns no numeric values or business rules to them:

- non-repetition policy horizon and time basis;
- intervention equivalence beyond exact canonical identity;
- override/exception authority roles and approval process;
- authentication, RBAC, and identity enforcement;
- whether `PAUSED` changes intervention or outcome clocks;
- late outcome observation tolerance;
- early outcome finalization;
- persistence, migration, correction, and rollback policy beyond immutable provenance requirements.

## 8. Locked/source-supported decisions

- Project 1 supplies recovery strategy/context; Project 2 owns execution; Project 3 consumes Project 2 strictly read-only.
- Phase 1 routing, planning, scoring, verification, guardrails, forecast semantics, and approval behavior remain frozen.
- A valid intervention start is `intervention_started_at`; evaluation lasts 60 calendar days.
- Baseline is the preceding 56-day period using the original methodology; outcome uses the original recent 14-day observation window.
- Target is `TARGET_UPLIFT_PCT = 30.1%`, not causal uplift; recovery uses the formulas above and preserves original clipping/guard behavior.
- Weekly checkpoints preserve the original intervention-relative convention.
- `REJECTED`, `EXPIRED`, and execution `CANCELLED` semantics are explicit; valid `COMPLETED` and execution-evidenced `FAILED` flow through `OUTCOME_PENDING` to `EVALUATED`.
- State comes from persisted immutable transition-event replay; active/repetition guards, checkpoint evidence, provenance, deterministic joins, and deterministic fallback are mandatory.
- Phase 2 excludes LLM/RAG, adaptive ML, autonomous decisions, and autonomous intervention.

## 9. Phase 1 invariants

Phase 2 must not change the Phase 1 router, planner, scorer, verifier, centralized approval guardrails, forecast contract (`AVAILABLE`, `NO_DATA`, `ERROR`), Project 2 audit adapter, JSONL recommendation behavior, dependency pins, APIs, or tests. It must not import Project 2 code, mutate Project 2 data, guess undocumented endpoints, or synthesize campaign, forecast, checkpoint, or outcome history. Approval-required recommendations remain human-gated.

## 10. Phase 2 acceptance criteria

1. The approved canonical identity is recorded and used consistently, with no invented equivalence rules.
2. Schemas, IDs, provenance, immutable transitions, and deterministic replay are tested.
3. Terminal states and `PAUSED` transitions behave as specified.
4. Separate clocks correctly derive the 60-day window, 56-day baseline, recent 14-day observation, and weekly checkpoints.
5. Formula inputs, target context, and original guards/clipping are auditable; forecast-only evidence cannot establish success.
6. Active and non-repetition guards produce deterministic, auditable results.
7. Evidence states include `NOT_DUE`, `PARTIAL`, `SUFFICIENT`, `INSUFFICIENT`, `INVALID`, and `CONTRADICTORY` as applicable.
8. Recommendation → approval → intervention → checkpoint → outcome joins are identity-, time-, and provenance-validated.
9. Insufficient evidence falls back deterministically to review/monitor.
10. Project 2 remains strictly read-only; no synthetic campaign history or autonomous intervention is possible.
11. All frozen Phase 1 tests and invariants remain green and unchanged.

## 11. Phase 2 implementation order

A. **Policy decisions** — approve identity, unresolved horizons, equivalence, authority, authentication, pause, and observation policies.

B. **Schemas/contracts** — version IDs, events, timestamps, provenance, read-only Project 2 inputs, outcomes, and evidence statuses.

C. **Intervention registry/state** — persist immutable transitions and rebuild state deterministically.

D. **Clocks** — implement separate store/intervention clocks and locked due windows after policy decisions.

E. **Active/repetition guards** — enforce active-intervention and approved non-repetition behavior, failing closed when identity is unresolved.

F. **Checkpoints** — record the original intervention-relative weekly checkpoint convention and evidence quality.

G. **Outcome evaluator** — implement the 60-day window, 56-day baseline, recent 14-day observation, target context, and source-preserved calculation guards.

H. **Evidence sufficiency** — implement all supported evidence states and deterministic review/monitor fallback.

I. **Evaluation/metrics** — implement provenance-validated joins, integrity metrics, diagnostics, fixtures, acceptance tests, and rollout/rollback review.

No step introduces LLM/RAG, adaptive ML, autonomous decision-making, or changes to frozen Phase 1 logic.

## 12. Future contributor handoff

Before writing code, read this specification, `docs/PROJECT_BLUEPRINT.md`, and `docs/PHASE_1_COMPLETION.md`. Obtain explicit policy approval for every unresolved item. The approved canonical `InterventionKey` should be treated as locked. Do not infer business semantics from generic retail practice or add equivalence rules from field names.

The first implementation review must include deterministic fixtures for active, repeated, paused, rejected, expired, cancelled, completed, and failed interventions; checkpoint gaps; not-due and contradictory outcomes; forecast `NO_DATA` versus `ERROR`; and insufficient-evidence fallback. Keep Project 2 read-only, preserve provenance, and keep every Phase 1 invariant frozen.
