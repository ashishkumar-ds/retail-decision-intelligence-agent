# Retail Decision Intelligence Agent

A deterministic decision system for retail store recovery: it recommends,
measures, and re-decides commercial actions on underperforming retail stores.
The decision path is pure code; every recommendation is recomputable by hand
and citable by ID. This file defines the project's language. It is a glossary
only — how things work lives in `docs/`, what words mean lives here.

## Language

### Decisions and evidence

**Recommendation**:
The deterministic engine's output for one store at one point in time: which
action the recovery plan calls for (e.g. `EXTEND_INTERVENTION`,
`PAUSE_INTERVENTION`), with the evidence it was computed from. Exactly one
"latest" recommendation per store.
_Avoid_: suggestion, prediction, output

**Recovery**:
Progress of the store's pre-registered recovery plan within its 60-day
window, expressed as **Recovery %**. A *plan-progress* measurement — it says
nothing about whether a campaign caused the change.
_Avoid_: uplift, campaign performance

**Measured Uplift**:
The observed effect of a completed intervention: recent observed sales vs
the same store's own 56-day baseline, as **Measured Uplift %**. An
*intervention-effect* measurement.
_Avoid_: recovery, performance, lift (unqualified)

**Health Score**:
A 0–100 score derived from Recovery % and its velocity. Input to the engine;
never a measure of campaign effect.
_Avoid_: store score, performance score

**Evidence**:
A specific, citable record backing a claim: a recommendation record, an
outcome evaluation, or a methodology source. Evidence is always attached to
an ID (`rec:`, `outcome:`, `src:`).
_Avoid_: data, context, signals

**Evidence State**:
The evaluator's verdict on whether measured data is trustworthy enough to
act on: `SUFFICIENT`, `PARTIAL`, `INSUFFICIENT`, `NOT_DUE`, `CONTRADICTORY`,
`INVALID`.
_Avoid_: confidence, quality score

**Measured Uplift %** — see Measured Uplift; **Recovery %** — see Recovery.

### The loop

**Recovery Loop**:
The system's core cycle: recommend → approve → execute → intervene → measure
→ re-decide. A loop "closes" when a second decision provably differs from the
first because of measured evidence.
_Avoid_: feedback cycle, automation loop

**Human Gate**:
The structural requirement that budget-affecting decisions pass a human
before any action. Enforced in code (fail-closed 503 without credentials),
never by prompt or policy.
_Avoid_: approval flow, sign-off

**Approval**:
A human decision on one recommendation, recorded in the audit ledger with
the authenticated principal (`decided_by`) and a double gate (guardrails +
verifier re-run at decision time).
_Avoid_: sign-off, accept, click

**Execution**:
The gated, idempotent, reversible action taken on an **approved**
recommendation. One execution per approval (idempotency key); reversible via
a journal event. Exists only after an Approval.
_Avoid_: deployment, apply, trigger

**Intervention**:
The registered, lifecycle-tracked instance of a campaign action on one store
(`APPROVED → ACTIVE → COMPLETED → …`). The thing that gets **executed** and
**measured**.
_Avoid_: campaign, experiment (an intervention *carries* a campaign_id)

**Campaign**:
The retail marketing program (in the external campaign system) that an
Intervention applies to a store. Referenced by `campaign_id` only; owned
externally.
_Avoid_: intervention (the Intervention is *our* lifecycle record; the
Campaign is *theirs*)

**Advisory**:
An LLM-drafted triage suggestion for the human reviewer, grounded in the
same evidence and structurally incapable of acting (never auto-applied,
writes nothing). Strictly above the Human Gate.
_Avoid_: recommendation (reserved for the engine's deterministic output)

### Trust and integrity

**Grounding**:
The requirement that every number and citation in an explanation trace to an
evidence ID in the store's own records. Enforced by fail-closed guards.
_Avoid_: accuracy, fact-check

**Recomputability**:
The guarantee that any recommendation can be re-derived by hand from its
cited evidence. The reason the decision path has no model calls.
_Avoid_: determinism (too generic), reproducibility

**Audit Trail**:
The append-only records (recommendation log, approval ledger, execution
journal) that make every decision reconstructible. Never rewritten, never
deleted.
_Avoid_: logs (too generic), history

**Decision Trajectory**:
The embedded stage-by-stage record of one engine run (route → plan → score →
verify → approval-check) attached to its recommendation.
_Avoid_: reasoning trace, chain of thought

### Roles and vocabulary boundaries

**Reviewer**:
The human at the Human Gate. The only actor who can turn a Recommendation
into an Approval.
_Avoid_: user, admin, manager

**Principal**:
The authenticated identity behind an Approval (a named token user, or
`shared-token`). Recorded as `decided_by`; distinct from any self-claimed
`actor` field.
_Avoid_: user, approver (role), account

**Off-Path**:
Describes anything that cannot influence a decision (the LLM layers, the
pre-filter, root-cause tags): off-path components may inform humans, never
decisions.
_Avoid_: side feature, auxiliary

**Golden Cases**:
The 22 pinned scenarios that gate the decision behavior in CI; behavior
changes require recalibrating them in the same commit.
_Avoid_: test fixtures, snapshots
