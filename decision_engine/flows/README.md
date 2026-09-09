# Declarative flow specs — documentation contract, not loaded code

These specs describe each decision flow the router can emit, in SKILL.md
style. They are **drift-checked, not executed**: `decision_engine/planner.py`
remains the executable source of truth (its `PLANS` dict is deliberately code,
not prose), and `scripts/check.py` fails the build if these specs drift from
the emittable routes (missing spec for a route, or a spec for a route the
engine cannot emit).

Rule provenance ("one home per rule"): executable rules live ONLY in code
(`planner.py`, `scorer.py`, `guardrails/`); these files describe them for
reviewers and future contributors and must never introduce a rule that code
does not enforce. If you want flows to become loaded artifacts (read by the
planner at runtime), that is a deliberate design change — wire it together
with the existing drift gate so the two cannot diverge.
