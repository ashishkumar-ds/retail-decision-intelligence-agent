"""Tests for the executive status board (the stakeholder surface).

The board must answer the product's three manager questions deterministically:
which stores are recovering, which need intervention, where the campaign is
working — and why it is not working where it is not. Classification is a pure
function over persisted records; every store lands in exactly one bucket.
"""
from __future__ import annotations

from presentation.board import (
    BUCKET_INTERVENE,
    BUCKET_NO_DATA,
    BUCKET_RECOVERING,
    BUCKET_WATCH,
    BUCKET_WORKING,
    build_board,
    classify_store,
    latest_by_store,
    render_board_html,
    why_not_working,
)


def _rec(store_id: int = 1, **overrides) -> dict:
    base = {
        "store_id": store_id,
        "recommendation": "CONTINUE",
        "confidence": 0.9,
        "store_health_score": 85.0,
        "recovery_pct": 4.0,
        "days_remaining": 30,
        "requires_human_approval": False,
        "reason": "on track",
        "recommendation_id": f"recommendation-{store_id}",
    }
    base.update(overrides)
    return base


def _outcome(assessment="MEETS_TARGET", uplift=4.2, did=3.5, state="CONFIRMED") -> dict:
    return {"outcome_evidence": {
        "target_assessment": assessment, "actual_uplift_pct": uplift,
        "causal_evidence": {"assessment_state": state, "did_uplift_pct": did},
    }}


def test_working_well_on_health_and_on_outcome():
    assert classify_store(_rec()) == BUCKET_WORKING
    assert classify_store(_rec(store_health_score=55.0, recovery_pct=1.0, **_outcome())) == BUCKET_WORKING


def test_recovering_positive_momentum_mid_band():
    rec = _rec(recommendation="MONITOR", store_health_score=56.7, recovery_pct=1.0)
    assert classify_store(rec) == BUCKET_RECOVERING


def test_no_data_lands_in_insufficient_data_not_intervention():
    # NEEDS_REVIEW is approval-gated, but the no-data route must surface as
    # insufficient data (flagged for analyst review), never as "act now".
    rec = _rec(recommendation="NEEDS_REVIEW", store_health_score=0.0,
               recovery_pct=0.0, requires_human_approval=True,
               forecast_signal_available=False)
    assert classify_store(rec) == BUCKET_NO_DATA


def test_gated_stores_need_intervention():
    rec = _rec(recommendation="ESCALATE", store_health_score=35.0,
               recovery_pct=-2.0, requires_human_approval=True)
    assert classify_store(rec) == BUCKET_INTERVENE


def test_watch_is_flat_mid_band_without_gating():
    rec = _rec(recommendation="MONITOR", store_health_score=50.0, recovery_pct=-0.5)
    assert classify_store(rec) == BUCKET_WATCH
def test_every_store_lands_in_exactly_one_bucket():
    records = [
        _rec(1), _rec(2, recommendation="MONITOR", store_health_score=56.7, recovery_pct=1.0),
        _rec(3, recommendation="ESCALATE", store_health_score=30.0, recovery_pct=-5.0,
             requires_human_approval=True),
        _rec(4, recommendation="NEEDS_REVIEW", store_health_score=0.0, recovery_pct=0.0,
             requires_human_approval=True, forecast_signal_available=False),
        _rec(5, recommendation="MONITOR", store_health_score=50.0, recovery_pct=-1.0),
    ]
    board = build_board(records)
    assert board["total_stores"] == 5
    assert sum(board["counts"].values()) == 5
    assert board["counts"] == {
        BUCKET_INTERVENE: 1, BUCKET_WORKING: 1, BUCKET_RECOVERING: 1,
        BUCKET_WATCH: 1, BUCKET_NO_DATA: 1,
    }


def test_why_negative_lift_names_market_drift():
    rec = _rec(**_outcome(assessment="NEGATIVE", uplift=-2.0, did=-6.0, state="REFUTED"))
    why = why_not_working(rec)
    assert "negative lift (-2.0%" in why
    assert "market drift" in why
    assert "controls outperformed" in why


def test_why_insufficient_evidence_states_the_window():
    rec = _rec(**_outcome(assessment="NOT_DUE", uplift=None, did=None, state=None))
    assert "56-day baseline" in why_not_working(rec)


def test_why_falls_back_to_record_reason():
    assert why_not_working(_rec(reason="deadline pressure")) == "deadline pressure"


def test_no_speculative_why_for_working_stores():
    board = build_board([_rec(1)])
    assert board["questions"]["where_it_is_not_working_and_why"] == []


def test_latest_record_wins_per_store():
    records = [_rec(1, recovery_pct=-3.0), _rec(1, recovery_pct=4.0)]
    board = build_board(records)
    assert board["total_stores"] == 1
    assert board["buckets"][BUCKET_WORKING][0]["recovery_pct"] == 4.0


def test_decision_status_reflects_pending_and_decided():
    records = [_rec(1, decided_at="2026-01-01", approved=True),
               _rec(2, recommendation="ESCALATE", store_health_score=30.0,
                    recovery_pct=-5.0, requires_human_approval=True)]
    board = build_board(records, pending_store_ids={2})
    assert board["buckets"][BUCKET_WORKING][0]["decision_status"] == "approved"
    assert board["buckets"][BUCKET_INTERVENE][0]["decision_status"] == "awaiting_decision"


def test_questions_answer_the_three_manager_questions():
    records = [
        _rec(1, **_outcome()),                                   # working
        _rec(2, recommendation="MONITOR", store_health_score=56.7, recovery_pct=1.0),  # recovering
        _rec(3, recommendation="PAUSE_INTERVENTION", store_health_score=30.0,
             recovery_pct=-6.0, requires_human_approval=True, **_outcome(
                 assessment="NEGATIVE", uplift=-2.0, did=-6.0, state="REFUTED")),  # not working
    ]
    q = build_board(records)["questions"]
    assert q["where_campaign_is_working"] == [1]
    assert q["which_stores_are_recovering"] == [2]
    assert q["which_stores_need_intervention"] == [3]
    (why,) = q["where_it_is_not_working_and_why"]
    assert why["store_id"] == 3 and "market drift" in why["why"]


def test_latest_by_store_ignores_non_integer_ids():
    assert latest_by_store([_rec(1), {"store_id": "x"}])[1]["store_id"] == 1


# --- board surfaces the choice architecture (guardrails) -----------------------

def test_intervention_entry_carries_choice_architecture():
    rec = _rec(3, recommendation="PAUSE_INTERVENTION", store_health_score=30.0,
               recovery_pct=-6.0, days_remaining=10,
               requires_human_approval=True, **_outcome(
                   assessment="NEGATIVE", uplift=-2.0, did=-6.0, state="REFUTED"))
    entry = build_board([rec])["buckets"][BUCKET_INTERVENE][0]
    assert entry["default_action"] == "PAUSE_INTERVENTION"
    assert entry["fallback_action"] == "MONITOR"
    assert entry["risk"] == "reversible"
    assert entry["cost_of_inaction"] is not None
    assert "negative" in entry["cost_of_inaction"]


def test_non_intervention_entries_omit_choice_architecture():
    entry = build_board([_rec(1)])["buckets"][BUCKET_WORKING][0]
    assert "default_action" not in entry
    assert "risk" not in entry
    assert "cost_of_inaction" not in entry


def test_html_board_shows_decision_options_on_intervention_rows():
    records = [
        _rec(1),  # working - no decision column
        _rec(3, recommendation="PAUSE_INTERVENTION", store_health_score=30.0,
             recovery_pct=-6.0, days_remaining=10,
             requires_human_approval=True, **_outcome(
                 assessment="NEGATIVE", uplift=-2.0, did=-6.0, state="REFUTED")),
    ]
    page = render_board_html(build_board(records))
    assert "Decision (default → fallback / risk)" in page
    assert "PAUSE_INTERVENTION → MONITOR / reversible" in page
    assert "cost of" in page or "With no action" in page


def test_html_view_escapes_and_renders_all_buckets():
    records = [
        _rec(1), _rec(3, recommendation="ESCALATE", store_health_score=30.0,
                      recovery_pct=-5.0, requires_human_approval=True,
                      reason="<script>alert(1)</script>"),
    ]
    page = render_board_html(build_board(records))
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "Needs intervention" in page
    assert "Campaign working well" in page
