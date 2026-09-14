#!/usr/bin/env python3
"""Golden-case evaluation runner for the deterministic decision engine.

Replays every case in ``golden_cases.GOLDEN_CASES`` through
``score_and_recommend`` (offline - no HTTP, no API keys) and compares the
outcome against the pinned expectations. Every run is appended to
``logs/eval_runs.jsonl`` so decision quality is auditable over time.

Run: ``python evaluation/run_evals.py`` -> exit 0 if all cases pass,
exit 1 with a mismatch report otherwise. ``--quiet`` prints only the summary.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from decision_engine.scorer import StoreSignal, score_and_recommend  # noqa: E402
from decision_engine.simulator import simulate_intervention  # noqa: E402
from evaluation.golden_cases import (  # noqa: E402
    GOLDEN_CASES,
    SIMULATION_CASES,
    GoldenCase,
    SimulationCase,
)

EVAL_LOG_PATH = Path("logs/eval_runs.jsonl")


def run_case(case: GoldenCase) -> dict:
    """Run one golden case; returns a result record with per-assert outcomes."""
    signal = StoreSignal(
        store_id=1,  # synthetic store - cases are store-agnostic
        baseline_forecast=case.baseline_forecast,
        current_forecast=case.current_forecast,
        days_elapsed=case.days_elapsed,
        days_remaining=case.days_remaining,
        forecast_signal_available=case.forecast_signal_available,
    )
    result = score_and_recommend(
        signal,
        outcome_evidence=case.outcome_evidence,
        causal_evidence=case.causal_evidence,
        store_context=case.store_context,
    )

    failures: list[str] = []
    checks = {
        "recommendation": (result["recommendation"], case.expected_recommendation),
        "requires_human_approval": (result["requires_human_approval"], case.expected_approval),
        "store_health_score": (result["store_health_score"], case.expected_health),
        "confidence": (result["confidence"], case.expected_confidence),
        "scale_up_eligible": (result.get("scale_up_eligible"), case.expected_scale_up_eligible),
        "diversification": (
            (result.get("diversification") or {}).get("action"),
            case.expected_diversification,
        ),
    }
    for field_name, (actual, expected) in checks.items():
        if expected is None:
            continue
        if field_name == "store_health_score":
            actual = round(float(actual), 1)
        if actual != expected:
            failures.append(f"{field_name}: expected {expected!r}, got {actual!r}")

    return {
        "case_id": case.case_id,
        "passed": not failures,
        "failures": failures,
        "actual_recommendation": result["recommendation"],
        "actual_health_score": result["store_health_score"],
        "actual_confidence": result["confidence"],
        "tags": case.tags,
    }


def run_all(quiet: bool = False) -> tuple[int, int, list[dict]]:
    results = [run_case(case) for case in GOLDEN_CASES]
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed

    if not quiet:
        for r in results:
            status = "PASS" if r["passed"] else "FAIL"
            line = f"[{status}] {r['case_id']} -> {r['actual_recommendation']} (health {r['actual_health_score']}, conf {r['actual_confidence']})"
            print(line)
            for failure in r["failures"]:
                print(f"       {failure}")

    print(f"\nGolden evals: {passed}/{len(results)} passed"
          + (f" ({failed} FAILED)" if failed else " - decision behavior matches the pinned calibration."))

    EVAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EVAL_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "run_at": datetime.now(timezone.utc).isoformat(),
            "total": len(results), "passed": passed, "failed": failed,
            "failed_case_ids": [r["case_id"] for r in results if not r["passed"]],
        }) + "\n")
    return passed, failed, results


def run_sim_case(case: SimulationCase) -> dict:
    """Run one simulator golden case against the pinned expectations."""
    result = simulate_intervention(
        1, case.observations, case.started_day,
        pre_window_days=56, evaluation_window_days=60,
    )
    guardrail = (result.get("simulation") or {}).get("projected", {}).get(
        "guardrail", {}).get("assessment_state", "UNAVAILABLE")
    checks = {
        "evidence_state": (result["evidence_state"], case.expected_evidence_state),
        "guardrail_state": (guardrail, case.expected_guardrail_state),
        "coverage_days": (
            (result.get("simulation") or {}).get("baseline", {}).get("coverage_days"),
            case.expected_coverage_days,
        ),
        "baseline_mean": (
            (result.get("simulation") or {}).get("baseline", {}).get("mean_daily_sales"),
            case.expected_baseline_mean,
        ),
    }
    failures = []
    for field_name, (actual, expected) in checks.items():
        if expected is None:
            continue
        if field_name == "baseline_mean":
            actual = round(float(actual), 1)
        if actual != expected:
            failures.append(f"{field_name}: expected {expected!r}, got {actual!r}")
    return {"case_id": case.case_id, "passed": not failures, "failures": failures,
            "tags": case.tags}


def run_sim_all() -> tuple[int, int, list[dict]]:
    results = [run_sim_case(case) for case in SIMULATION_CASES]
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    if not failed:
        print(f"Simulator evals: {passed}/{len(results)} passed - pre-approval backtest matches the pinned prior.")
    else:
        print(f"Simulator evals: {passed}/{len(results)} passed ({failed} FAILED)")
        for r in results:
            if not r["passed"]:
                print(f"       {r['case_id']}: {'; '.join(r['failures'])}")
    with EVAL_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "run_at": datetime.now(timezone.utc).isoformat(),
            "kind": "simulator", "total": len(results),
            "passed": passed, "failed": failed,
            "failed_case_ids": [r["case_id"] for r in results if not r["passed"]],
        }) + "\n")
    return passed, failed, results


if __name__ == "__main__":
    _quiet = "--quiet" in sys.argv
    _passed, _failed, _ = run_all(quiet=_quiet)
    _sim_passed, _sim_failed, _ = run_sim_all()
    sys.exit(1 if (_failed or _sim_failed) else 0)
