"""Tests for the learned policy V2 challenger (decision_engine/policy_v2.py)."""
import pytest

from decision_engine.policy_v2 import (
    evaluate_policies,
    featurize,
    predict_action_values,
    recommend_v2,
    train_policy_v2,
)


def _row(health=30.0, velocity=0.1, margin=0.25, avail="UNKNOWN",
         action="EXTEND_INTERVENTION", realised=10.0):
    return {"health": health, "velocity": velocity, "margin_rate": margin,
            "demand_vs_supply": avail, "action": action, "realised_margin": realised}


def test_featurize_buckets_coarsely():
    assert featurize(20.0, -1.0, 0.05, "POSSIBLE_SUPPLY_GAP") == (
        "low", "non_positive", "thin", "POSSIBLE_SUPPLY_GAP")
    assert featurize(90.0, 0.5, 0.30, "DEMAND") == ("high", "positive", "rich", "DEMAND")
    assert featurize(50.0, 0.0, None, None) == ("mid", "non_positive", "rich", "UNKNOWN")


def test_train_and_recommend_learns_cell_preference():
    rows = [_row(action="RETARGET_SEGMENT", realised=50.0) for _ in range(5)]
    rows += [_row(action="EXTEND_INTERVENTION", realised=5.0) for _ in range(5)]
    policy = train_policy_v2(rows)
    assert policy["trained_rows"] == 10
    rec = recommend_v2(policy, ("low", "positive", "rich", "UNKNOWN"))
    assert rec["recommended_action"] == "RETARGET_SEGMENT"
    assert rec["expected_margin"] == pytest.approx(50.0)


def test_thin_cells_back_off_to_global_means():
    rows = [_row(health=20.0, action="EXTEND_INTERVENTION", realised=40.0) for _ in range(5)]
    rows.append(_row(health=90.0, action="PAUSE_INTERVENTION", realised=-5.0))
    policy = train_policy_v2(rows)
    values = predict_action_values(policy, ("high", "positive", "rich", "UNKNOWN"))
    # single PAUSE sample backs off to its global mean, not the cell sample
    assert values["PAUSE_INTERVENTION"] == pytest.approx(-5.0)
    assert values["EXTEND_INTERVENTION"] == pytest.approx(40.0)


def test_evaluate_policies_reports_lift():
    train = [_row(action="RETARGET_SEGMENT", realised=50.0) for _ in range(5)]
    train += [_row(action="EXTEND_INTERVENTION", realised=5.0) for _ in range(5)]
    policy = train_policy_v2(train)
    held_out = [_row(action="EXTEND_INTERVENTION", realised=5.0) for _ in range(4)]
    result = evaluate_policies(policy, held_out)
    assert result["rows"] == 4
    assert result["v1_mean_margin"] == pytest.approx(5.0)
    assert result["v2_mean_margin"] == pytest.approx(50.0)
    assert result["lift_v2_over_v1"] == pytest.approx(45.0)


def test_empty_training_is_safe():
    policy = train_policy_v2([])
    assert policy["trained_rows"] == 0
    rec = recommend_v2(policy, ("mid", "positive", "mid", "UNKNOWN"))
    assert rec["recommended_action"] == "CONTINUE"  # deterministic tie-break
    assert evaluate_policies(policy, [])["rows"] == 0
    with pytest.raises(ValueError):
        predict_action_values({"policy": "v1"}, ("mid", "positive", "mid", "UNKNOWN"))
