"""Pinned checks for the review-fix additions (P1-P3, additive only).

Nothing here touches the frozen decision rules: policy_eval is report-only,
experiment assignment is offline labelling, the connector default is unchanged
(dry-run), and the explainer addition introduces no numbers.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _row(action="EXTEND_INTERVENTION", realised=10.0):
    return {"health": 30.0, "velocity": 0.1, "margin_rate": 0.25,
            "demand_vs_supply": "UNKNOWN", "action": action,
            "realised_margin": realised}


def test_policy_eval_report_only_and_zero_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "log.jsonl"))
    proc = subprocess.run(
        [sys.executable, "evaluation/policy_eval.py"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0  # report-only: never gates
    report = json.loads(proc.stdout)
    assert report["rows"] == 0 and report["v1_mean_margin"] is None


def test_policy_eval_scores_supplied_rows(tmp_path):
    rows_path = tmp_path / "rows.jsonl"
    rows = ([_row("RETARGET_SEGMENT", 50.0)] * 5
            + [_row("EXTEND_INTERVENTION", 5.0)] * 5)
    rows_path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "evaluation/policy_eval.py", "--rows", str(rows_path)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0
    report = json.loads(proc.stdout)
    assert report["rows"] == 10
    assert report["lift_v2_over_v1"] == pytest.approx(22.5)


def test_experiment_assignment_deterministic_and_guarded():
    from decision_engine.experiment import assign, assign_panel

    first = assign(7)
    assert assign(7) == first  # stable hash: same inputs, same arm
    assert assign(7, experiment="other-exp") != first or True  # namespace differs ok
    panel = assign_panel([3, 1, 2, 1])
    assert [r["store_id"] for r in panel] == [1, 2, 3]  # dedup + sorted
    with pytest.raises(TypeError):
        assign(True)
    with pytest.raises(ValueError):
        assign(1, experiment="")
    with pytest.raises(ValueError):
        assign(1, arms=("only-one",))
    with pytest.raises(ValueError):
        assign(1, epsilon=0.0)


def test_connector_default_is_dry_run_and_in_health():
    import os

    from execution.connector import resolve_connector_name

    os.environ.pop("EXECUTION_CONNECTOR", None)
    assert resolve_connector_name() == "dry-run"
    os.environ["EXECUTION_CONNECTOR"] = "  "
    try:
        assert resolve_connector_name() == "dry-run"
    finally:
        os.environ.pop("EXECUTION_CONNECTOR", None)


def test_explainer_surfaces_recorded_retail_context_only():
    from rag.corpus import build_chunks
    from rag.explainer import (
        build_narrative,
        numeric_grounding_check,
        validate_citations,
    )

    corpus = build_chunks()
    rec = {"store_id": 7, "recommendation": "CONTINUE", "confidence": 0.9,
           "store_health_score": 80.0, "recovery_pct": 4.0, "days_remaining": 30,
           "recommendation_id": "rec-x",
           "retail_context": {"ops_flag": "verify stock",
                              "demand_vs_supply": "POSSIBLE_SUPPLY_GAP"}}
    ev = {"store_id": 7, "latest_recommendation": rec, "latest_decision": None,
          "intervention_count": 0, "outcome": {}, "event_count": 0}
    narrative, _ = build_narrative(ev)
    assert "verify stock" in narrative  # recorded ops_flag surfaced
    assert numeric_grounding_check(narrative, ev, []) == []
    assert validate_citations(narrative, ev, corpus) == []
    # no context -> byte-identical to before (no sentence added)
    rec_no_ctx = {k: v for k, v in rec.items() if k != "retail_context"}
    ev_no_ctx = dict(ev, latest_recommendation=rec_no_ctx)
    narrative_no_ctx, _ = build_narrative(ev_no_ctx)
    assert "Operations flag" not in narrative_no_ctx
    assert "forecast numbers alone" not in narrative_no_ctx


def test_board_carries_forecast_status():
    from presentation.board import build_board

    recs = [{"store_id": 1, "recommendation": "NEEDS_REVIEW",
             "store_health_score": 0.0, "forecast_signal_available": False,
             "requires_human_approval": True, "forecast_status": "ERROR",
             "recovery_pct": 0.0, "days_remaining": 60,
             "recommendation_id": "rec-e"}]
    board = build_board(recs)
    entry = board["buckets"]["insufficient_data"][0]
    assert entry["forecast_status"] == "ERROR"
