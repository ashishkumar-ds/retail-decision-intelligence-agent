"""Pytest wrapper: the golden eval suite runs in CI with the unit tests.

Kept separate from ``evaluation/run_evals.py`` (the operator-facing CLI with
the JSONL audit trail); this module reuses the same runner so CI and local
runs can never diverge.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from evaluation.golden_cases import (  # noqa: E402
    GOLDEN_CASES,
    SIMULATION_CASES,
    GoldenCase,
    SimulationCase,
)
from evaluation.run_evals import run_case, run_sim_case  # noqa: E402


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c.case_id for c in GOLDEN_CASES])
def test_golden_case(case: GoldenCase):
    result = run_case(case)
    assert result["passed"], f"{case.case_id}: {result['failures']}"


@pytest.mark.parametrize("case", SIMULATION_CASES, ids=[c.case_id for c in SIMULATION_CASES])
def test_simulation_case(case: SimulationCase):
    result = run_sim_case(case)
    assert result["passed"], f"{case.case_id}: {result['failures']}"


def test_golden_suite_covers_all_recommendation_families():
    """The suite must exercise every decision family, not just the happy path."""
    covered = {c.expected_recommendation for c in GOLDEN_CASES}
    required = {
        "CONTINUE", "MONITOR", "EXTEND_INTERVENTION", "ESCALATE",
        "NEEDS_REVIEW", "PAUSE_INTERVENTION",
        "RETARGET_SEGMENT", "TIMING_SHIFT", "REALLOCATE_BUDGET",
    }
    assert required <= covered, f"golden suite missing coverage for: {sorted(required - covered)}"
