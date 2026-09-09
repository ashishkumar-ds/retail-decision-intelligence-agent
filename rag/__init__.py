"""Grounded "why" RAG subsystem (Priority 4).

Two-tier grounding contract:
- Tier 1 (authoritative): the agent's own evidence trail - recommendation
  log, Phase-2 registry events, outcome/causal evidence. Decision
  explanations cite record IDs from Tier 1 only.
- Tier 2 (supporting): a vetted methodology corpus that explains why the
  methods are valid. Attributed, never decision-bearing.

Guardrail: narrative claims are checked against cited evidence - every
number in a generated explanation must exist in the cited Tier-1 evidence
or the cited Tier-2 chunk (numeric grounding guard, fail-closed).
"""
