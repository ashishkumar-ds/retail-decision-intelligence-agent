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


def ranked_queue_matches_exhaustive_composition(pending, ranked):
    """The seam contract: one call equals rank + enrich verbatim. Internal seam."""
    from app.monitor import is_campaign_working as _working
    from app.monitor import rank_attention as _rank
    expected = _rank(list(pending))
    for rec in expected:
        rec["campaign_working"] = _working(rec)
    return ranked == expected

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


def test_ranked_attention_queue_is_one_call_for_rank_plus_enrich():
    from app.monitor import ranked_attention_queue
    pending = [
        _rec(3, recommendation="ESCALATE", store_health_score=30.0,
             recovery_pct=-5.0, requires_human_approval=True),
        _rec(9, recommendation="EXTEND_INTERVENTION", store_health_score=45.0,
             recovery_pct=-1.0, requires_human_approval=True),
    ]
    ranked = ranked_attention_queue(pending)
    assert [r["store_id"] for r in ranked] == [3, 9]
    assert all("campaign_working" in r for r in ranked)
    assert ranked_queue_matches_exhaustive_composition(pending, ranked)
    assert pending[0].get("campaign_working") is None  # inputs are not mutated


def test_working_well_on_health_and_on_causal_outcome():
    assert classify_store(_rec()) == BUCKET_WORKING
    assert classify_store(_rec(store_health_score=55.0, recovery_pct=1.0, **_outcome())) == BUCKET_WORKING


def test_raw_meets_target_without_confirmed_did_is_not_working_well():
    """Board mirrors the monitor: MEETS_TARGET alone is working but not causal.

    Without CONFIRMED DiD (or on-track health), the store is recovering/watch,
    never working_well on raw own-baseline lift alone.
    """
    rec = _rec(store_health_score=55.0, recovery_pct=1.0,
               **_outcome(assessment="MEETS_TARGET", state="UNAVAILABLE", did=None))
    assert classify_store(rec) == BUCKET_RECOVERING
    rec = _rec(store_health_score=55.0, recovery_pct=1.0,
               **_outcome(assessment="MEETS_TARGET", uplift=4.2, did=1.0, state="REVIEW_ZONE"))
    assert classify_store(rec) == BUCKET_RECOVERING


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


def test_no_data_variants_without_explicit_flag_land_in_insufficient_data():
    # note: live pending-store / legacy records omit forecast_signal_available
    # or carry only forecast_status — they must not fall through to intervention.
    missing_flag = _rec(recommendation="NEEDS_REVIEW", store_health_score=0.0,
                        recovery_pct=0.0, requires_human_approval=True)
    assert classify_store(missing_flag) == BUCKET_NO_DATA
    error_status = _rec(recommendation="NEEDS_REVIEW", store_health_score=0.0,
                        recovery_pct=0.0, requires_human_approval=True,
                        forecast_status="ERROR")
    assert classify_store(error_status) == BUCKET_NO_DATA
    # An explicit True flag still means a real gated decision, not no-data.
    gated_review = _rec(recommendation="NEEDS_REVIEW", store_health_score=0.0,
                        recovery_pct=0.0, requires_human_approval=True,
                        forecast_signal_available=True, forecast_status="AVAILABLE")
    assert classify_store(gated_review) == BUCKET_INTERVENE


def test_needs_decision_counts_gated_rows_across_buckets():
    from presentation.board import BUCKET_LABELS, build_board
    records = [
        _rec(1, recommendation="ESCALATE", store_health_score=30.0,
             recovery_pct=-5.0, requires_human_approval=True),
        _rec(2, recommendation="NEEDS_REVIEW", store_health_score=0.0,
             recovery_pct=0.0, requires_human_approval=True,
             forecast_signal_available=False),
        _rec(3),  # ungated CONTINUE
    ]
    board = build_board(records)
    assert board["needs_decision"] == 2
    assert BUCKET_LABELS["insufficient_data"] == "Needs review (no data)"


def test_gated_stores_need_intervention():
    rec = _rec(recommendation="ESCALATE", store_health_score=35.0,
               recovery_pct=-2.0, requires_human_approval=True)
    assert classify_store(rec) == BUCKET_INTERVENE


def test_watch_is_flat_mid_band_without_gating():
    rec = _rec(recommendation="MONITOR", store_health_score=50.0, recovery_pct=-0.5)
    assert classify_store(rec) == BUCKET_WATCH


def test_no_data_store_is_still_a_decision_a_human_owns():
    """The 47-store bucket is review workload, not dead data.

    A no-forecast-signal store cannot be evaluated, so the choice is review the
    gap or leave it alone - and that choice is gated, carries the same option
    pair as every other gated row, and states its own reason. It is a data
    review, not a budget approval: the label must not claim committed money.
    """
    rec = _rec(7, recommendation="NEEDS_REVIEW", store_health_score=0.0,
               recovery_pct=0.0, forecast_status="NO_DATA",
               forecast_signal_available=False)
    board = build_board([rec])
    entry = board["buckets"][BUCKET_NO_DATA][0]
    assert entry["requires_human_approval"] is True
    assert entry["default_action"] == "NEEDS_REVIEW"
    assert entry["fallback_action"] == "MONITOR"
    assert entry["risk"] == "reversible"
    assert entry["commitment_label"] == "No spend — review only"
    assert "no forecast signal" in entry["why_not_working"].lower()
    assert board["needs_decision"] == 1


def test_legacy_gated_row_without_the_flag_still_counts():
    """A log row written before the flag existed is still a gated action.

    guardrails' policy is the source of truth: keying the count on the
    persisted flag let the board read "1 needs a decision" while 47 gated
    stores sat beside it.
    """
    legacy = _rec(7, recommendation="NEEDS_REVIEW", store_health_score=0.0,
                  recovery_pct=0.0, forecast_status="NO_DATA",
                  forecast_signal_available=False)
    legacy.pop("requires_human_approval")  # written before the flag existed
    board = build_board([legacy, _rec(8)])  # plus an ungated CONTINUE
    assert board["needs_decision"] == 1
    assert board["buckets"][BUCKET_NO_DATA][0]["default_action"] == "NEEDS_REVIEW"
    assert "default_action" not in board["buckets"][BUCKET_WORKING][0]


def test_board_reports_data_freshness_not_poll_interval():
    records = [_rec(1, generated_at="2026-10-11T00:00:00+00:00"),
               _rec(2, generated_at="2026-10-11T06:30:00+00:00")]
    assert build_board(records)["as_of"] == "2026-10-11T06:30:00+00:00"
    assert build_board([_rec(3)])["as_of"] is None  # nothing to date-stamp


def test_every_row_carries_its_own_state_word():
    """A surface that groups buckets still prints the true state per row.

    The stakeholder tiles merge on-track/momentum/watch into one group; the
    row's own word is what stops that merge from overstating the stores in it.
    """
    records = [
        _rec(1),  # on track
        _rec(2, recommendation="MONITOR", store_health_score=56.7, recovery_pct=1.0),
        _rec(3, recommendation="MONITOR", store_health_score=50.0, recovery_pct=-0.5),
    ]
    buckets = build_board(records)["buckets"]
    assert buckets[BUCKET_WORKING][0]["state"] == "On track"
    assert buckets[BUCKET_RECOVERING][0]["state"] == "Building momentum"
    assert buckets[BUCKET_WATCH][0]["state"] == "Watch"


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
    # ...and the machine token is served with the sentence a board prints
    assert board["buckets"][BUCKET_WORKING][0]["decision_status_label"] == "Approved"
    assert board["buckets"][BUCKET_INTERVENE][0]["decision_status_label"] == "Awaiting a decision"


def test_html_board_prints_readable_freshness_and_status():
    """A human-readable page prints sentences, not machine tokens."""
    from presentation.board import format_as_of
    records = [_rec(1, generated_at="2026-10-18T14:02:00+00:00"),
               _rec(2, recommendation="ESCALATE", store_health_score=30.0,
                    recovery_pct=-5.0, requires_human_approval=True,
                    generated_at="2026-10-18T14:02:00+00:00")]
    page = render_board_html(build_board(records, pending_store_ids={2}))
    assert "data as of 18 Oct 2026, 14:02 UTC" in page
    assert "Awaiting a decision" in page
    assert "awaiting_decision" not in page
    # a stamp we cannot parse is shown as served, never silently dropped
    assert format_as_of("not-a-date") == "not-a-date"
    assert format_as_of(None) == ""
    assert format_as_of("2026-10-18T14:02:00+00:00") == "18 Oct 2026, 14:02 UTC"
    # a non-zero offset keeps its own clock, never converted to UTC
    assert format_as_of("2026-10-18T14:02:00+01:00") == "18 Oct 2026, 14:02 UTC+01:00"


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
    assert "Decision (recommended → alternative · commitment)" in page
    # the option pair, the commitment and the counterfactual all reach the page
    assert "Pause the intervention → Monitor only · Undoable this cycle" in page
    assert "With no action" in page
    # ...in stakeholder wording: the raw engine code is not a board headline
    assert "PAUSE_INTERVENTION" not in page
    # the ungated row keeps an empty decision cell rather than a blank prompt
    assert "<td class='decision'>-</td>" in page


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
