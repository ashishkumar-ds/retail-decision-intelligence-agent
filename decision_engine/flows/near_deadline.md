# Flow: near_deadline route

## When to apply
The router emitted `near_deadline`: forecast signal is available but the
intervention window is close to its deadline (few `days_remaining`).

## Steps (executable keys, in order)
1. `score_and_recommend` — same deterministic core as `standard`; the scorer's
   rules weigh the shortened window (velocity and confidence rules differ).

## Rules
- The shortened window is a scoring input, never a reason to bypass gates.
- Deadline pressure never lowers the approval bar: every recommendation in
  `guardrails.APPROVAL_REQUIRED_RECOMMENDATIONS` still waits for a human.

## Never
- Auto-execute an approval-gated action because time is short.
