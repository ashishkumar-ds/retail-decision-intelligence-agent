# Flow: feedback_loop_redecision

## When to apply
A Phase 2 intervention outcome has been evaluated from the actuals feed
(`source="actuals_replay"`) and the next recommendation for the store is
being produced. This flow wraps `standard`/`near_deadline` - it changes the
*evidence in*, not the engine out.

## Steps (executable keys, in order)
1. Resolve the latest evaluated outcome per store from the Phase 2 registry
   (`app/main._outcome_evidence_by_store`).
2. `score_and_recommend` — scoring consumes the outcome:
   - `NEGATIVE` lift -> `PAUSE_INTERVENTION` (approval-gated in `guardrails/`);
   - `MEETS_TARGET` -> confidence boost on the continuing recommendation;
   - `REVIEW_ZONE` -> tempered confidence;
   - inconclusive evidence changes nothing but is surfaced in the reason.
3. Verify, persist, and gate exactly as in the base flows.

## Rules
- The outcome feeds the decision; it never overrides one. The deterministic
  rules in `scorer.py` remain the only decision authority.
- Coverage gaps in the actuals feed are evidence
  (`DataLimitation`), never backfilled or averaged away.

## Never
- Re-decide a store from a stale or mixed-source outcome; replay and posted
  observations must never be mixed in one evaluation.
