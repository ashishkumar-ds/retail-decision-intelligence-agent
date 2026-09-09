# Flow: standard route

## When to apply
The router emitted `standard`: forecast signal is available, the intervention
window is not near its deadline. This is the default sweep path for every
store in the campaign audit.

## Steps (executable keys, in order)
1. `score_and_recommend` — compute recovery_pct, velocity, health_score;
   apply the deterministic decision rules in `decision_engine/scorer.py`.

## Rules
- Outcome evidence (latest evaluated Phase 2 outcome) is consumed by scoring;
  `NEGATIVE` lift -> `PAUSE_INTERVENTION`, `MEETS_TARGET` -> confidence boost,
  `REVIEW_ZONE` -> tempered confidence. Inconclusive evidence changes nothing.
- The verifier gate runs on every produced recommendation before persistence.
- Recommendations in `guardrails.APPROVAL_REQUIRED_RECOMMENDATIONS` are
  queued for human approval; nothing executes on its own.

## Never
- Skip the verifier, backfill a coverage gap, or let the LLM change a decision.
