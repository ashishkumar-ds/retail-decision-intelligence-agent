---
source_type: playbook
title: Pre-Approval Checklist
url: in-repo note (authored for this project)
author: this project / this repo
year: 2026
license: in-repo playbook note (authored for this project)
tier: tier-2
---
# Pre-Approval Checklist

What a reviewer should check before approving, in the order the system itself
checks it. This checklist describes the real gates — the same checks run
automatically at decision time — plus the judgment calls no gate can make.

## The decision-time gate re-runs everything (automatic)

Approval is not a rubber stamp: at the moment of decision the system re-runs
two gates and refuses (fail-closed) if either fails:

1. **Still requires human approval** — the recommendation must still be one
   the guardrail policy gates to a human. A rule change between
   recommendation and decision must not retroactively legitimise an ungated
   action.
2. **Verifier over the full record** — the label is one of the known actions
   (CONTINUE, MONITOR, EXTEND_INTERVENTION, RETARGET_SEGMENT, TIMING_SHIFT,
   PAUSE_INTERVENTION, ESCALATE, NEEDS_REVIEW, REALLOCATE_BUDGET); the
   confidence is inside 0.0–1.0; a reason exists; the human-approval flag
   still matches the recommendation.

A refused approval records nothing. The 409 response carries the failed
checks.

## What only the human can check (judgment)

- **Was the forecast signal real?** The record says when the decision was
  made on forecast numbers alone, and when the feed was delayed ("feed
  delayed" is a data problem, not a store problem). A decision made without
  its strongest input deserves a closer look at the inputs before approval,
  not after.
- **Was a key input assumed?** Where the record lacks a margin rate, the
  policy assumes the mid tier (0.25). Availability defaults to UNKNOWN when
  unmeasured. Approving on an assumed input is a business call, not a
  technical one.
- **Is the confidence fresh?** Health/confidence reliability metrics are
  time-lagged and verdict-gated by design. Check "generated at" (on the
  provenance line) against today's date: an old record approved as-is is a
  decision about yesterday's store.
- **Does the reason survive reading aloud?** The engine wording is the
  deterministic ground truth the LLM narrative is judged against. If the
  engine's own reason is thin, no rephrasing fixes that.
- **What does the advisory say — and what did it refuse to say?** The LLM
  suggestion is grounded against this store's record only; a suggestion that
  degraded (budget spent, layer off, grounding refused) says so. Treat a
  degraded suggestion as absent, not as disagreement.

## The one thing to never do

Never approve to make a pending row disappear. The queue exists so a human
decides; the gate re-runs regardless, and the ledger keeps the decision,
actor, and gate audit permanently.
