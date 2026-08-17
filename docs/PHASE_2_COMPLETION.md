# Phase 2 Completion Record

## 1. Phase 2 objectives

Phase 2 adds governed intervention memory and deterministic outcome evaluation around the frozen Phase 1 decision engine. The MVP records intervention lifecycle events, reconstructs state from immutable persisted events, enforces exact canonical identity repetition checks, tracks weekly checkpoints, evaluates outcomes with the locked 60-day/56-day/14-day methodology, and preserves Project 2 as a strictly read-only source of execution evidence.

## 2. Implemented components

The final Phase 2 MVP includes:

- canonical `InterventionKey` contracts
- append-only intervention registry persistence
- lifecycle transition events and replay
- deterministic state reconstruction from persisted events
- active-intervention guard
- exact-key repetition guard
- weekly checkpoint generation and classification
- 60-day outcome evaluation
- evidence sufficiency classification
- recommendation → approval → intervention → checkpoint → outcome joining
- read-only Project 2 provenance lookup

## 3. InterventionKey

The approved canonical Phase 2 identity is:

```text
InterventionKey = {
    store_id: int,
    intervention_type: str,
    target_segment: str | null,
    campaign_variant: str | null,
    strategy_version: str
}
```

The MVP uses this key as the canonical identity of a governed intervention definition. It does not identify a run, recommendation, approval, checkpoint, or outcome.

The key is serialized deterministically using the approved field order above. `store_id`, `intervention_type`, and `strategy_version` are required. `target_segment` and `campaign_variant` may be null. Missing or unknown values are not silently inferred.

## 4. Lifecycle and replay

Intervention lifecycle state is represented by immutable persisted transition events. Reconstruction is deterministic:

- events are ordered by timestamp
- original file order is used as the deterministic tie-break when timestamps are equal
- invalid transitions remain visible in diagnostics but are not applied

The implemented lifecycle includes:

- `RECOMMENDED`
- `APPROVED`
- `ACTIVE`
- `PAUSED`
- `COMPLETED`
- `FAILED`
- `REJECTED`
- `EXPIRED`
- `CANCELLED`
- `OUTCOME_PENDING`
- `EVALUATED`

`REJECTED` and `EXPIRED` are terminal. `CANCELLED` is a terminal execution state. `PAUSED` is explicit and can resume to `ACTIVE` or terminate.

## 5. Active/repetition guards

The MVP implements two deterministic guards:

- the active-intervention guard blocks a new intervention when a reconstructed intervention for the same store is `APPROVED`, `ACTIVE`, or `PAUSED` with no end timestamp
- the non-repetition guard compares canonical `InterventionKey` values exactly and blocks repeats on exact-key equality only

No additional intervention-equivalence engine is introduced.

## 6. Checkpoints

Weekly checkpoints are deterministic records tied to the intervention-relative schedule.

The MVP behavior is:

- checkpoint cadence is 7 days
- each checkpoint is classified as `DUE`, `OBSERVED`, `MISSED`, or `INVALID`
- checkpoint evidence remains visible and does not by itself conclude the outcome

## 7. 60-day outcome evaluation

Outcome evaluation uses the locked Phase 2 timing model:

- 60 calendar day evaluation window from `intervention_started_at`
- 56-day baseline window immediately before intervention start
- 14-day recent observation window at the end of the 60-day period

The evaluator uses arithmetic means over the baseline and recent windows.

The locked recovery calculation is preserved:

```text
actual_uplift_pct = (recent_observation - baseline) / baseline * 100
recovery_pct_of_target = actual_uplift_pct / 30.1 * 100
```

The evaluator never marks an outcome successful when evidence is insufficient.

## 8. Evidence states

The Phase 2 MVP classifies evidence using the documented states:

- `NOT_DUE`
- `PARTIAL`
- `SUFFICIENT`
- `INSUFFICIENT`
- `INVALID`
- `CONTRADICTORY`

These states are used to keep missing, late, invalid, and contradictory facts visible instead of inventing success or failure.

## 9. Provenance validation

The final MVP join validates provenance deterministically across recommendation, approval, intervention, checkpoints, and outcome.

The join rejects records when the following conflict:

- `store_id`
- canonical `InterventionKey`
- `campaign_id`, when both sides provide it
- `timing_window`, when both sides provide it

When provenance conflicts are present, the join returns a deterministic invalid/contradictory-style result instead of silently carrying forward first-seen values.

## 10. Project 2 read-only boundary

Project 2 remains the execution system of record. Phase 2 reads Project 2 evidence through the existing audit-log adapter only and does not write Project 2 records, infer new Project 2 APIs, or manufacture execution history.

The MVP provenance lookup is read-only and leaves the source audit file unchanged.

## 11. Test results

The current validation result is:

```text
38 passed, 2 skipped
```

The skipped tests are the existing Phase 1 FastAPI endpoint tests that depend on the unavailable FastAPI/Pydantic runtime stack in this environment.

## 12. Known limitations and unresolved policies

Known MVP limitations:

- checkpoint cadence is fixed at 7 days
- join validation is deterministic but intentionally narrow
- arithmetic means are used for baseline and recent windows
- JSONL remains the persistence mechanism

Unresolved policy decisions remain as documented in `docs/PHASE_2_SPEC.md`, including:

- non-repetition policy horizon and time basis
- intervention equivalence beyond exact canonical identity
- override/exception authority roles and approval process
- authentication, RBAC, and identity enforcement
- whether `PAUSED` changes intervention or outcome clocks
- late outcome observation tolerance
- early outcome finalization
- persistence, migration, correction, and rollback policy beyond immutable provenance requirements

## 13. Phase 1 freeze confirmation

Phase 1 is frozen and unchanged. The Phase 2 MVP did not modify the Phase 1 router, planner, scorer, verifier, guardrails, forecast adapter, campaign adapter, dependencies, APIs, or existing Phase 1 tests.

## 14. Future capabilities

Future work remains outside the Phase 2 MVP and is not implemented here. Potential Phase 3 or later capabilities include:

- adaptive policy calibration
- governed outcome analytics at larger scale
- richer provenance reporting
- improved rollback and correction workflows
- additional operator tooling

These are future capabilities only and are not part of the implemented Phase 2 MVP.
