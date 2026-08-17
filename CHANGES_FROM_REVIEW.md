# Fixes applied during review (Aug 2026)

This version merges two independent branches of work on the same base commit
(`cbfacd5`):

1. This backup's own uncommitted work: an opt-in HTTP audit source for
   Project 2 campaign data (`CAMPAIGN_AUDIT_API_URL`), with strict schema
   validation, endpoint allowlisting, and provenance labeling. That work is
   unchanged here.
2. Three fixes to the Phase 2 outcome-evaluation lifecycle, ported from a
   separate review pass and applied on top. Detailed below. All 69 tests
   pass after merging (`PYTHONPATH=. pytest -q`): 64 pre-existing + 5 that
   were failing due to fix #1 below.

## 1. Test fixture bug — `tests/test_phase2_integration.py`

`integration_context` mocked `get_audit_log` to return a bare `dict` instead
of the `list[dict]` the real function contracts to return. Iterating a dict
yields its keys as strings, so downstream `.get()` calls on campaign runs
crashed. Fixed by wrapping the loaded JSON in a list.

## 2. `outcome_pending` event timestamp — `app/main.py`

`evaluate_phase2_outcome` stamped the `outcome_pending` transition event with
`datetime.now(timezone.utc)` (wall-clock) instead of the caller-supplied
`as_of`. Because this system is event-sourced and replay sorts by
`occurred_at`, any caller using backdated/future-dated timestamps (a normal
pattern for backfills, batch jobs, or tests simulating time passing) could
get an event that sorts out of order relative to sibling events, causing the
transition to be silently rejected as invalid during reconstruction — with no
error surfaced to the caller. Fixed by deriving `occurred_at` from `as_of`
when provided, falling back to wall-clock only when it isn't.

## 3. `evaluate` transition ran before provenance validation — `app/main.py`

The lifecycle transition to `EVALUATED` (and its irreversible append-only
persist) was gated only on `calculation.evidence_state` — the raw statistical
sufficiency of the outcome observations. The provenance/consistency `join`
check (which validates `campaign_id`/`timing_window` agreement across
recommendation, approval, intervention, checkpoints, and outcome) ran *after*
that persist. A conflicting checkpoint could therefore produce an API
response reporting `"evidence_state": "CONTRADICTORY"` while the durable,
append-only state had already been advanced to `EVALUATED` — a split-brain
between the response contract and the source of truth, with no way to walk
it back given the append-only design.

Fixed by reordering: the join is now computed and validated *before* the
`evaluate` event is persisted, so the lifecycle only advances to `EVALUATED`
when `join.evidence_state == "SUFFICIENT"`, not just the raw calculation.

Note: fix #3 was latent and effectively masked by fix #2 in earlier testing —
the timestamp ordering bug accidentally dropped the `evaluate` event as an
invalid transition in the scenario that exercises this path, which kept
response and persisted state coincidentally in sync for the wrong reason.
Fixing #2 first is what exposes #3; both are fixed here together.

## Cleanup pass

The `as_of`-or-wall-clock pattern introduced by fixes #2 and #3 duplicated an
existing inline expression (already used once at the top of
`evaluate_phase2_outcome`). Extracted into `_phase2_as_of_or_now(payload)`
next to `_parse_phase2_timestamp`, and all three call sites now use it
directly inline (matching the existing style in this function, which favors
inlining single-use expressions over intermediate variables).

## Cleanup pass

The `as_of`-or-wall-clock pattern introduced by fixes #2 and #3 duplicated an
existing inline expression (already used once at the top of
`evaluate_phase2_outcome`). Extracted into `_phase2_as_of_or_now(payload)`
next to `_parse_phase2_timestamp`, and all three call sites now use it
directly inline (matching the existing style in this function, which favors
inlining single-use expressions over intermediate variables).

## Repo-wide cleanup pass

Applied KISS/DRY across every source file, not just the diff above:

- `phase2/evaluator.py` reimplemented `_validate_aware_datetime` verbatim,
  duplicating the version already in `phase2/contracts.py`. Removed the
  duplicate and imported the shared one instead.
- `app/main.py`: `uuid` (stdlib) was grouped with third-party imports
  (`requests`) instead of the stdlib group — moved.
- `app/main.py`: `approve_recommendation` and `reject_recommendation` each
  called `utcnow_iso()` twice to stamp two fields (`approved_at`/`decided_at`,
  `rejected_at`/`decided_at`) that represent the same decision instant.
  Two separate wall-clock calls could theoretically disagree by a few
  microseconds for values that should be identical. Now computed once and
  reused.
- `tools/campaign_tool.py` and `phase2/evaluator.py`: fixed a few inconsistent
  blank-line counts between top-level functions (1 or 3 blank lines instead
  of the file's own convention of 2).

No behavior changes in this pass — 69/69 tests still pass throughout, and no
new abstractions were introduced beyond reusing what already existed.

## Compatibility note

The HTTP-audit-source changes (`tools/campaign_tool.py`, `get_recommendations`
error handling) and the Phase 2 fixes (`app/main.py`'s `evaluate_phase2_outcome`)
touch disjoint code paths and were confirmed to merge cleanly with no
conflicts.
