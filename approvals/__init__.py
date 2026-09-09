"""Durable, double-gated approval ledger (merchant-agent `changes.py` pattern).

The merchant-agent stages every change into a ledger and re-runs its
guardrail checks both at staging AND at apply time. This package carries the
same pattern into this project's approve/reject flow:

- ``ledger.append_decision`` records every approve/reject decision in an
  append-only JSONL ledger (``logs/approval_ledger.jsonl``), stamped with
  the operator and the re-verification result.
- ``ledger.decision_gate`` is the double gate: the recommendation must
  (a) still require human approval per ``guardrails`` and (b) still pass
  ``decision_engine.verifier.verify_recommendation`` *at decision time* -
  not only when it was first recommended. A recommendation whose rules
  changed between recommendation and approval cannot slip through on the
  stale evaluation.
"""
from approvals.ledger import decision_gate, read_decisions  # noqa: F401

__all__ = ["decision_gate", "read_decisions"]
