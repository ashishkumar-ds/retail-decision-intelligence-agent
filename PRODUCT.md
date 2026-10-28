# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary user: a non-technical commercial stakeholder for the retail recovery
program, opening the board occasionally (not daily). Their job on a visit:
answer one question per store — decide now, cannot evaluate yet, or nothing to
do — and trust the number behind it without re-deriving it.

Secondary audience (confirmed by use, not interview): technical reviewers of
the repo, who verify the decision system itself. The dashboard is not designed
for them; the repo's ADRs and `decision_engine/` are.

## Product Purpose

The retail decision intelligence agent is a deterministic decision system for
retail store recovery: it recommends, measures, and re-decides commercial
actions on underperforming stores. The stakeholder board (`app/`, the surface
this product record governs) is the read-only stakeholder view of that system:
one honest answer per store, grouped as **Need a decision** /
**Needs review (no data)** / **No action needed** (display grouping only — the
backend's buckets are unchanged, and each grouped row still prints its own
state word so "No action needed" never overstates a flat or declining store).

Success is the stakeholder leaving knowing exactly which store needs them, why,
and what evidence backs it — even when the answer is "nothing to do".

## Positioning

Every recommendation is pure code, recomputable by hand, and citable by
`rec:` / `outcome:` / `src:` ID. A neighboring analytics dashboard can show
numbers; it cannot truthfully copy: recomputable verdicts, missing evidence
failing closed, budget moving only behind a human approval, and the LLM
measured off the decision path (never on it).

## Operating Context

- Live deployment: Vercel (board) + Render (API, free tier — first load may
  wake it ~30–60s; the footer reports "API waking") + Supabase (storage).
- State stores a named campaign (from env, surfaced once as the hero identity,
  not repeated per row) against a 60-day evaluation window.
- The stakeholder may arrive with zero context: the board must carry its own
  vocabulary — Recovery %, Measured Uplift %, Counterfactual Uplift %, Health
  Score, Evidence State — following the glossary in `CONTEXT.md` verbatim.
- Writes (approving/escalating a recommendation) are token-gated API operations
  outside this surface's job; the board is read-only by design.

## Capabilities and Constraints

- Deterministic engine output is the only source of recommendations; the board
  never derives its own verdicts.
- Honest-capacity rule: counts and captions must not overstate confidence —
  e.g. inside "No action needed", *recovering* → "building momentum",
  *working well* → "on track", *monitor* → "flat, no action" are three
  different reassurances and stay split.
- Health/confidence reliability metrics are time-lagged, non-circular, and
  verdict-gated; the UI may display them but must not fake freshness.
- Connection status is one quiet footer probe (no polling loops, no motion).
- Undecided (recorded, not invented): a formal WCAG target level; current
  code has had a11y review fixes but no recorded standard.

## Brand Commitments

Binding as of this record (user-confirmed): the board reads as a **dunnhumby**
product surface. The `/dunnhumby.svg` wordmark asset, the lowercase wordmark +
brand-product lockup in the brand rail, Roboto body type (matching
dunnhumby.com, replacing the earlier DM Sans), the restrained editorial
palette, and the storefront-outline mark with card/shelf layout language are
part of the product identity. Future dashboard work preserves this system
unless the user starts an explicit redesign.

## Evidence on Hand

- Live board: https://retail-decision-intelligence-agent.vercel.app/
- Live API health: https://retail-decision-intelligence-agent.onrender.com/health
- Screenshot: `docs/images/dashboard-board.png` (board preview; volatile
  counts in screenshots — do not copy counts into copy).
- Glossary: `CONTEXT.md` (canonical vocabulary). Methodology: `docs/adr/`.

## Product Principles

1. One answer per store, honestly split — decide now / cannot evaluate /
   nothing to do, never a blur between them.
2. Numbers earn trust by provenance: citable IDs, recomputable verdicts,
   evidence state surfaced, missing evidence fails closed.
3. The vocabulary is the product: Recovery % means plan progress, Measured
   Uplift % means intervention effect — never swap, weaken, or inflate them
   for tone.
4. Read-only calm: the stakeholder surface reports and frames; every write
   stays behind explicit, token-gated approval flows.
5. dunnhumby editorial restraint: precise type, quiet borders, honest color
   — brand lives in details, not decoration.

## Accessibility & Inclusion

Current state: keyboard operability and focus styling have had a design-review
pass; no WCAG level has been formally adopted. Future work may set one.
