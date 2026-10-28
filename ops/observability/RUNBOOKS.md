# Alert runbooks

One page per rule in `alerts.yml`: what it means, how to diagnose, what to
do, and when to ignore it. All metrics come from `GET /metrics` (bearer
token required); all state referenced here is in the JSONL audit trail,
the approval ledger, or the pending-approval store.

## RetailDecisionsConcentratedInOnePrincipal (warning)

One principal made >80% of decisions (minimum 5) over the window.
- **Diagnose:** check `retail_decisions_by_principal` — one label dominating
  means a shared token is in use or the queue routes to one reviewer.
  Compare `APPROVAL_TOKENS` assignments with who is actually on shift.
- **Fix:** issue named per-reviewer tokens (`scripts/issue_token.py`),
  revoke the shared one. No code change.
- **Ignore when:** a one-person on-call stretch is known and intended.

## RetailSweepFailures (critical)

At least one full sweep cycle raised within the hour.
- **Diagnose:** read the `[SWEEP SCHEDULER] sweep failed` log line; the
  `last_error` field on `/health` names the cause. Usual suspects in order:
  campaign audit feed unreachable (Project 2 asleep), forecast API 4xx/5xx,
  no store universe (empty audit log).
- **Fix:** wake or re-seed the upstream (run Project 2's rollout in test
  mode, then `POST /recommendations/run`). The scheduler retries on its
  own; this alert means a whole cycle went by without success.
- **Ignore when:** never — a failing sweep means the board is going stale.

## RetailSweepStale (critical)

No successful sweep in 48h.
- **Diagnose:** check `/health` → `scheduler`: `enabled` false means
  `SWEEP_ENABLED` is unset (redeploy wiped env or it was never set);
  `consecutive_failures` high with `last_error` set means repeated upstream
  failure (see RetailSweepFailures); process restarts without the env var
  mean the same as disabled.
- **Fix:** set `SWEEP_ENABLED=1` in the Render dashboard and redeploy; or
  fix the upstream named in `last_error`. Verify with
  `scripts/verify_deployment.sh`.
- **Ignore when:** the service is intentionally stood down.

## RetailPendingApprovalBacklog (warning)

More than 50 recommendations awaiting a human decision for 2h.
- **Diagnose:** open `/attention-queue` — a handful of large stores or a
  genuinely broad wave? Check `retail_approval_latency_seconds_avg`: rising
  latency means reviewers are falling behind; flat latency with a high
  count means a burst (e.g. right after a wipe-and-resweep).
- **Fix:** clear the queue in Approvals; if bursts recur, shorten the sweep
  interval or widen the reviewer rotation. Never auto-approve to clear it.
- **Ignore when:** post-restore bursts that drain within the day.

## RetailOffpathLlmGuardRejectionSurge (warning)

Over 30% of LLM drafts vetoed by grounding gates (minimum 10 events/hour).
- **Diagnose:** nothing on the decision path is affected — the deterministic
  template is being served, which is why this needs an alert at all. Usual
  causes: substituted/upgraded model, drifted prompt, changed evidence
  block. See `rag/llm_telemetry.py` for per-layer reasons and
  `evaluation/llm_evals.py` for the pinned cases.
- **Fix:** pin the model back or recalibrate the prompt; re-run the LLM
  evals. If the evals pass and production still rejects, the evidence
  block changed shape — check `/why` payloads.
- **Ignore when:** the LLM layers are intentionally off (no events at all
  means no alert either).

## RetailOffpathLlmDegradedToTemplate (warning)

LLM layer errors absorbed by fail-closed fallback within the hour.
- **Diagnose:** check API key, base URL, provider status; confirm on
  `/health` that the layer is meant to be on (`llm_explanations` /
  `llm_advisory` flags).
- **Fix:** restore credentials or provider; or turn the layer off
  deliberately so the absence is a decision, not an accident.
- **Ignore when:** the layer is off by flag — then there is nothing to fire.
