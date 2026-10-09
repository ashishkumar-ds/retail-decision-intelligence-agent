#!/usr/bin/env python3
"""Offline V1-vs-V2 policy report (P1 profit objective, report-only).

Replays held-out rows with realised margins through the production V1
heuristic (``decision_engine/scorer.py`` outcome) and the ``policy_v2``
tabular challenger, then reports mean margin per policy plus V2 lift over V1
and over the do-nothing baseline.

Report-only by design (ADR-0001/0004): exit code is ALWAYS 0 — this never
gates CI and never changes a recommendation. Promotion of V2 to production
requires an offline win here PLUS versioning, review, and golden-case
recalibration in the same commit.

Rows: ``{"health", "recovery_pct", "velocity", "days_remaining",
"margin_rate", "demand_vs_supply", "action", "realised_margin"}`` — built from
the audit trail (recommendation + SUFFICIENT outcome) or synthetic history in
tests. With no rows the report states the data gap instead of inventing one.

Run: ``python evaluation/policy_eval.py [--rows PATH]`` where PATH is a JSONL
file of row mappings (one per line). Without ``--rows`` a zero-row report is
emitted (fail-open: the harness works before outcome history exists).

Score identity (money, not lift %): ``margin = baseline_daily_sales ×
window_days × lift_pct/100 × margin_rate − campaign_cost`` — the same formula
as ``phase2/budget_allocator.py`` and ``simulator.compare_candidate_actions``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from decision_engine.policy_v2 import (  # noqa: E402
    evaluate_policies,
    train_policy_v2,
)

REPORT_PATH = Path("logs/policy_eval.jsonl")


def load_rows(path: str | None) -> list[dict]:
    if not path:
        return []
    rows: list[dict] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                if isinstance(record, dict):
                    rows.append(record)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", default=None, help="JSONL file of training/eval rows")
    args = parser.parse_args()

    rows = load_rows(args.rows)
    if not rows:
        report = {
            "rows": 0,
            "v1_mean_margin": None,
            "v2_mean_margin": None,
            "lift_v2_over_v1": None,
            "lift_v2_over_baseline": None,
            "note": "no realised-margin rows supplied; challenger cannot be scored yet",
        }
    else:
        policy = train_policy_v2(rows)
        report = evaluate_policies(policy, rows)
        report["note"] = "offline replay on supplied rows; V1 remains production"
    print(json.dumps(report, indent=2))
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(report, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
