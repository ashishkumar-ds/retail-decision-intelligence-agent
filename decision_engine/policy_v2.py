"""Learned action-value policy V2 — offline challenger to the heuristic V1.

Governance (ADR-0001/0004): V1 (``decision_engine/scorer.py`` rules) remains
the production policy. This module NEVER decides on its own: it trains a
contextual action-value model from logged decision/outcome history and
predicts expected incremental margin per action for *offline comparison*
(``evaluation/policy_eval.py``) or as an attached ``policy_v2`` comparison
block on a V1 record. Promotion to production requires an offline win,
versioning, review and golden-case recalibration in the same commit.

Model (deliberately simple — a contextual bandit starter):

- Context is bucketed coarsely (health tier × velocity sign × margin tier ×
  availability flag) so per-cell statistics stay populated at N≈85 stores.
- Each cell keeps per-action mean realised margin with Laplace-style backoff
  to the global action mean when the cell is thin (``MIN_CELL_N``).
- ``recommend`` picks the highest-value action; ties break by a fixed action
  order (deterministic). Exploration is offline (epsilon-greedy flag for the
  experiment engine roadmap), never in production scoring.

Training rows: ``{"health", "recovery_pct", "velocity", "days_remaining",
"margin_rate", "demand_vs_supply", "action", "realised_margin"}`` — built from
the audit trail (recommendation + SUFFICIENT outcome) or synthetic history in
tests. Pure functions, deterministic, no model dependencies.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

POLICY_VERSION = "v2-tabular-1"
MIN_CELL_N = 3

ACTION_ORDER = (
    "CONTINUE",
    "MONITOR",
    "EXTEND_INTERVENTION",
    "RETARGET_SEGMENT",
    "TIMING_SHIFT",
    "PAUSE_INTERVENTION",
    "ESCALATE",
    "NEEDS_REVIEW",
    "REALLOCATE_BUDGET",
)


def featurize(
    health: float,
    velocity: float,
    margin_rate: float | None,
    demand_vs_supply: str | None,
) -> tuple[str, str, str, str]:
    """Bucket context coarsely so cells stay populated at small N."""
    health_tier = "low" if health < 40 else ("high" if health >= 70 else "mid")
    velocity_sign = "non_positive" if velocity <= 0 else "positive"
    rate = margin_rate if isinstance(margin_rate, (int, float)) else 0.25
    margin_tier = "thin" if rate < 0.15 else ("rich" if rate >= 0.25 else "mid")
    availability = demand_vs_supply if demand_vs_supply in ("POSSIBLE_SUPPLY_GAP", "DEMAND") else "UNKNOWN"
    return (health_tier, velocity_sign, margin_tier, availability)


def _row_features(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return featurize(
        float(row.get("health", 50.0)),
        float(row.get("velocity", 0.0)),
        row.get("margin_rate"),
        row.get("demand_vs_supply"),
    )


def train_policy_v2(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Fit per-cell, per-action mean realised margins with global backoff."""
    cell_stats: dict[str, dict[str, list[float]]] = {}
    global_sums: dict[str, float] = {}
    global_counts: dict[str, int] = {}
    n = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        action = row.get("action")
        margin = row.get("realised_margin")
        if action not in ACTION_ORDER or isinstance(margin, bool) or not isinstance(margin, (int, float)):
            continue
        cell = "|".join(_row_features(row))
        cell_stats.setdefault(cell, {}).setdefault(action, []).append(float(margin))
        global_sums[action] = global_sums.get(action, 0.0) + float(margin)
        global_counts[action] = global_counts.get(action, 0) + 1
        n += 1
    global_means = {a: global_sums[a] / global_counts[a] for a in global_sums}
    fallback = (sum(global_means.values()) / len(global_means)) if global_means else 0.0
    return {
        "policy": "v2",
        "version": POLICY_VERSION,
        "trained_rows": n,
        "cell_stats": {cell: {a: list(v) for a, v in actions.items()}
                       for cell, actions in cell_stats.items()},
        "global_means": global_means,
        "fallback": fallback,
    }


def predict_action_values(policy: Mapping[str, Any], features: tuple) -> dict[str, float]:
    """Expected margin per action for one bucketed context (backoff when thin)."""
    if not isinstance(policy, Mapping) or policy.get("policy") != "v2":
        raise ValueError("not a v2 policy artifact")
    cell_stats = policy.get("cell_stats") or {}
    global_means = policy.get("global_means") or {}
    fallback = float(policy.get("fallback", 0.0))
    cell = cell_stats.get("|".join(features), {})
    values: dict[str, float] = {}
    for action in ACTION_ORDER:
        samples = cell.get(action, [])
        if len(samples) >= MIN_CELL_N:
            values[action] = sum(samples) / len(samples)
        elif action in global_means:
            values[action] = float(global_means[action])
        else:
            values[action] = fallback
    return values


def recommend_v2(policy: Mapping[str, Any], features: tuple) -> dict[str, Any]:
    """Best action by learned value (deterministic tie-break by ACTION_ORDER)."""
    values = predict_action_values(policy, features)
    best = max(ACTION_ORDER, key=lambda a: (values[a], -ACTION_ORDER.index(a)))
    return {
        "recommended_action": best,
        "expected_margin": round(values[best], 2),
        "action_values": {a: round(v, 2) for a, v in values.items()},
        "policy_version": policy.get("version"),
    }


def evaluate_policies(
    policy: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    baseline_action: str = "CONTINUE",
) -> dict[str, Any]:
    """Offline V1-vs-V2 replay on held-out rows with realised margins.

    Each row carries the taken (V1) action's realised margin plus the context;
    V2 proposes its action for the same context and is credited with the
    cell-mean value of *its* action (conservative: the model's own estimate,
    not the oracle). Reports mean margin per policy and the lift of V2 over
    V1 and over the do-nothing baseline.
    """
    v1_total = v2_total = base_total = 0.0
    n = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        realised = row.get("realised_margin")
        if isinstance(realised, bool) or not isinstance(realised, (int, float)):
            continue
        features = _row_features(row)
        values = predict_action_values(policy, features)
        v1_total += float(realised)
        v2_total += values.get(recommend_v2(policy, features)["recommended_action"], 0.0)
        base_total += values.get(baseline_action, 0.0)
        n += 1
    if not n:
        return {"rows": 0, "v1_mean_margin": None, "v2_mean_margin": None,
                "lift_v2_over_v1": None, "lift_v2_over_baseline": None}
    v1_mean, v2_mean, base_mean = v1_total / n, v2_total / n, base_total / n
    return {
        "rows": n,
        "v1_mean_margin": round(v1_mean, 2),
        "v2_mean_margin": round(v2_mean, 2),
        "baseline_mean_margin": round(base_mean, 2),
        "lift_v2_over_v1": round(v2_mean - v1_mean, 2),
        "lift_v2_over_baseline": round(v2_mean - base_mean, 2),
        "policy_version": policy.get("version"),
    }
