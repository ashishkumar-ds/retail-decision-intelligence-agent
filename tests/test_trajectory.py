"""Tests for the per-decision trajectory (illustrated-agents pattern).

The trajectory is the deterministic cousin of the book's conversational
``Trajectory``: no wall-clock inside (determinism), embedded in the
recommendation record (citable by /why and the approval surface).
"""
from __future__ import annotations

from decision_engine.trajectory import DecisionTrajectory


def test_start_initializes_empty_trajectory_for_the_store():
    trajectory = DecisionTrajectory.start(store_id=7)
    assert trajectory.store_id == 7
    assert len(trajectory) == 0
    assert trajectory.to_record() == {"store_id": 7, "steps": []}


def test_add_is_chainable_and_preserves_run_order():
    trajectory = DecisionTrajectory.start(7).add("route", "done", "standard") \
        .add("plan", "done", "score_and_recommend") \
        .add("score", "done", "CONTINUE")
    assert [s["step"] for s in trajectory.steps] == ["route", "plan", "score"]
    assert trajectory.steps[0]["detail"] == "standard"


def test_to_record_is_deterministic_no_wall_clock():
    a = DecisionTrajectory.start(1).add("route", "done", "standard").to_record()
    b = DecisionTrajectory.start(1).add("route", "done", "standard").to_record()
    assert a == b
    assert "timestamp" not in str(a).lower()
