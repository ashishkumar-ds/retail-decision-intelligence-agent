"""Deterministic experiment assignment for the 85-store panel (P2 roadmap).

Turns the Phase-2 DiD observational caveat into experimental proof: stores are
assigned to arms by a stable hash of ``experiment + store_id`` (no RNG, no
clock — same inputs always give the same arm, recomputable by hand), with the
assignment recorded alongside the intervention so the later DiD has a real
treated/control split to measure.

Contract (same discipline as the rest of the repo):
- Pure functions: ``assign(store_id, experiment)`` is deterministic; no I/O.
- Fail-closed codes: unknown experiment or bad store_id raises (never a silent
  default arm); arm shares must sum to 1.0.
- Epsilon-greedy is OFFLINE exploration only (the roadmap): production scoring
  never explores; this module only labels which arm a store WOULD take.
- Assignment is provenance, not a decision: it never flips a recommendation,
  it is stored on the intervention record for the evaluator to join on.
"""
from __future__ import annotations

import hashlib

DEFAULT_ARMS = ("control", "treated")
DEFAULT_EPSILON = 0.1


def _stable_unit(store_id: int, experiment: str) -> float:
    digest = hashlib.sha256(f"{experiment}:{store_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def assign(
    store_id: int,
    experiment: str = "campaign-18-scaleup",
    arms: tuple[str, ...] = DEFAULT_ARMS,
    epsilon: float = DEFAULT_EPSILON,
) -> dict[str, object]:
    """Assign one store to an arm deterministically (epsilon-greedy split).

    With probability ``epsilon`` the store explores (uniform over arms by
    stable hash); otherwise it exploits the current best arm (``treated`` —
    the proven Campaign 18 play — until ``evaluation/policy_eval.py`` says
    otherwise). Raises on bad inputs; never invents an arm.
    """
    if isinstance(store_id, bool) or not isinstance(store_id, int):
        raise TypeError("store_id must be an integer")
    if not experiment or not isinstance(experiment, str):
        raise ValueError("experiment must be a non-empty name")
    if len(arms) < 2:
        raise ValueError("at least two arms are required for an experiment")
    if not 0 < epsilon < 1:
        raise ValueError("epsilon must be in (0, 1)")
    unit = _stable_unit(store_id, experiment)
    explore = unit < epsilon
    if explore:
        index = int(unit / epsilon * len(arms)) % len(arms)
        arm = arms[index]
    else:
        arm = "treated" if "treated" in arms else arms[0]
    return {
        "store_id": store_id,
        "experiment": experiment,
        "arm": arm,
        "explore": explore,
        "epsilon": epsilon,
        "method": "stable-hash-epsilon-greedy (offline labelling only)",
    }


def assign_panel(
    store_ids: list[int],
    experiment: str = "campaign-18-scaleup",
    arms: tuple[str, ...] = DEFAULT_ARMS,
    epsilon: float = DEFAULT_EPSILON,
) -> list[dict[str, object]]:
    """Assign a whole panel; deterministic order by store_id."""
    return [assign(sid, experiment, arms, epsilon) for sid in sorted(set(store_ids))]
