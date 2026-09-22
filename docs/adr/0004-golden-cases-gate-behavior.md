# Golden cases gate decision behavior; recalibration ships in the same commit

22 pinned business scenarios (`evaluation/golden_cases.py` +
`evaluation/run_evals.py`) define the decision behavior; CI fails if behavior
drifts from them. A deliberate recalibration must update the pinned cases in
the same commit, with the rationale stated — never quietly. The same rule
protects the pinned structure of `scripts/check.py`.

This rule was previously enforced only in a local (gitignored) agent-rules
file; it is recorded here so it travels with the public repository.
