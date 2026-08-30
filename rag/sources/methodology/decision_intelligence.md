# Decision Intelligence in This Agent

## Definition

Decision intelligence is the commercial application of data science and AI
to decision making — the framing used by Gartner (strategic technology
trend), building on Pratt & Zangari (2008) and popularized by Cassie
Kozyrkov at Google. In retail it spans assortment, pricing, marketing,
merchandising and supply chain decisions that are interlinked.

## This agent's stance

- Deterministic decision core: a rule chain with a recomputable health
  score and boundary-aware confidence; the same inputs always produce the
  same recommendation, and a verifier gates persistence.
- Human-gated autonomy: expensive or risky actions (escalate, extend,
  pause, retarget, timing shift, reallocate budget) require human approval
  through a bearer-token-guarded endpoint; without a configured token the
  endpoints refuse to serve at all (fail-closed).
- Closed loop: plan, execute, measure, re-decide. Evaluated outcomes feed
  back into scoring as evidence; inconclusive evidence changes nothing.
- Explanations are grounded: every "why" answer cites record IDs from the
  agent's own logs, plus methodology sources for the reasoning principles.
