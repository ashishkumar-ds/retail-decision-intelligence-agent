#!/usr/bin/env python3
"""Offline health/confidence reliability report over the audit trail.

Reads the append-only recommendation log and the phase-2 event registry (the
same env-configurable paths the API uses: ``RECOMMENDATION_LOG_PATH``,
``PHASE_2_INTERVENTION_LOG_PATH``), pairs each recorded score with the same
store's first *later* measured outcome (time-lagged, non-circular - see
``analytics/reliability.py`` for why the lag exists), and prints the
rank-check, the health bins and the verdict.

Deterministic for a given file set and exits 0 in every data state: the
verdict column carries "insufficient evidence" honestly instead of failing.

Usage:
    python evaluation/reliability_report.py                 # 28-day half-cycle lag
    python evaluation/reliability_report.py --min-lag-days 0   # offline demo data (21d span)
    python evaluation/reliability_report.py --json          # identical numbers as JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.reliability import compute_health_reliability  # noqa: E402
from memory.history import read_log  # noqa: E402
from phase2.registry import InterventionRegistry  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="health/confidence reliability report")
    parser.add_argument("--min-lag-days", type=float, default=None,
                        help="pairing lag in days (default: module default 28; "
                             "use 0 for the 21-day offline demo recordings)")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON only")
    args = parser.parse_args()

    kwargs = {}
    if args.min_lag_days is not None:
        kwargs["min_lag_days"] = args.min_lag_days
    recommendations = read_log()
    outcome_records = [event.to_record() for event in InterventionRegistry().read_events()]
    report = compute_health_reliability(recommendations, outcome_records, **kwargs)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return

    spearman = report["spearman"]
    for side, label in (("health_vs_measured_uplift", "health"),
                        ("confidence_vs_measured_uplift", "confidence")):
        entry = spearman[side]
        print(f"rho({label} vs measured uplift): "
              f"rho={entry['rho']} p={entry['p_value']} n={entry['n']}"
              + (f"  [{entry['note']}]" if entry["note"] else ""))
    lags = report["lag_days"]
    print(f"paired outcomes: {report['paired_outcome_count']} "
          f"(of {report['measured_outcome_events']} measured evaluate events)"
          + (f", lag {lags['min']}..{lags['max']}d (mean {lags['mean']}d)" if lags else ", none paired"))
    print(f"directional hit rate (uplift > 0): {report['directional_hit_rate']}")
    print("health bins:")
    for row in report["health_bins"]:
        print(f"  {row['bin']:>9}  n={row['measured_count']:<3} "
              f"mean_uplift={row['mean_measured_uplift_pct']} "
              f"positive_share={row['positive_share']}")
    print(f"verdict: {report['verdict']}")
    print(f"  {report['verdict_note']}")


if __name__ == "__main__":
    main()
