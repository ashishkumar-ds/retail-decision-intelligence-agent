# The decision path is pure code — no model calls

A "decision intelligence agent" that makes no model calls looks like a
contradiction, but it is the product's identity: every recommendation must be
recomputable by hand from cited evidence, and an LLM in the decision path
cannot guarantee that. LLMs are confined to off-path layers (narrative
rephrasing, advisory triage, retrieval pre-filtering, root-cause analytics)
that degrade to deterministic output on any failure and are structurally
unable to act.

## Considered Options

- LLM in the decision path (rejected: breaks recomputability and auditability,
  the properties the system exists for)
- LLM proposes, code disposes (rejected: the proposal still shapes the
  decision; only a fully deterministic path keeps the trajectory citable)
- Code decides, LLM explains (chosen)
