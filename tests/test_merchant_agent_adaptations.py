"""Tests for the commerce-agents merchant-agent pattern adaptations.

Covers the four adapted patterns in isolation (all offline, no network):
- rag/llm_explainer: static/dynamic prompt split + lexicon grounding gate
- tools/forecast_tool: DataLimitation on actuals coverage gaps
- approvals/ledger: decision-time double gate
- presentation/cards: typed, enriched payload builders
"""
from __future__ import annotations

import pytest

from approvals.ledger import decision_gate
from presentation.cards import (
    build_approval_preview,
    build_attention_digest,
    build_recommendation_card,
)
from rag.llm_explainer import (
    build_dynamic_context,
    build_lexicon,
    build_static_system,
    lexicon_check,
)
from tools.forecast_tool import DataLimitation, actuals_limitations

# --- llm_explainer: prompt split + lexicon gate -------------------------------

def test_static_system_prompt_is_byte_stable():
    assert build_static_system() == build_static_system()
    assert "rephrase" in build_static_system()


def test_dynamic_context_is_fenced_per_request_data():
    block = build_dynamic_context(1, "why?", "template", {"a": 1}, [])
    assert block.startswith("<evidence>")
    assert block.endswith("</evidence>")


def test_lexicon_accepts_engine_vocabulary():
    evidence = {"recovery_pct": 40.0}
    text = "Recovery is recovery_pct 40% and the store is behind."
    assert lexicon_check(text, evidence) == []


def test_lexicon_rejects_invented_engine_vocabulary():
    evidence = {"recovery_pct": 40.0}
    text = "The confidence_velocity metric improved and we hit review_target."
    violations = lexicon_check(text, evidence)
    assert "confidence_velocity" in violations
    assert "review_target" in violations


def test_build_lexicon_includes_evidence_keys():
    lexicon = build_lexicon({"some_new_metric": 1})
    assert "some_new_metric" in lexicon


# --- forecast_tool: DataLimitation --------------------------------------------

def test_no_observed_sales_is_a_limitation():
    limits = actuals_limitations({"start_day": 10, "end_day": 12, "observations": []})
    assert [lim.code for lim in limits] == ["no_observed_sales"]
    assert isinstance(limits[0], DataLimitation)


def test_coverage_gap_days_are_typed_evidence():
    envelope = {
        "start_day": 10,
        "end_day": 14,
        "observations": [
            {"day": 10, "date": "d10", "sales_value": 5.0},
            {"day": 14, "date": "d14", "sales_value": 6.0},
        ],
    }
    limits = actuals_limitations(envelope)
    assert len(limits) == 1
    assert limits[0].code == "coverage_gap"
    assert limits[0].detail["missing_days"] == [11, 12, 13]
    assert limits[0].to_record()["code"] == "coverage_gap"


def test_full_coverage_yields_no_limitations():
    envelope = {
        "start_day": 10,
        "end_day": 11,
        "observations": [
            {"day": 10, "date": "d10", "sales_value": 5.0},
            {"day": 11, "date": "d11", "sales_value": 6.0},
        ],
    }
    assert actuals_limitations(envelope) == []


# --- approvals/ledger: decision-time double gate ------------------------------

def _gated_record() -> dict:
    return {
        "store_id": 7,
        "recommendation": "ESCALATE",
        "confidence": 0.6,
        "reason": "underperforming",
        "requires_human_approval": True,
    }


def test_gate_allows_gated_verified_record():
    assert decision_gate(_gated_record())["allowed"] is True


def test_gate_refuses_ungated_recommendation():
    record = {**_gated_record(), "recommendation": "CONTINUE",
              "requires_human_approval": False}
    gate = decision_gate(record)
    assert gate["allowed"] is False
    assert gate["checks"]["still_requires_human_approval"] is False


def test_gate_refuses_flag_mismatch():
    record = {**_gated_record(), "requires_human_approval": False}
    gate = decision_gate(record)
    assert gate["allowed"] is False
    assert gate["checks"]["decision_time_verification_passed"] is False


# --- presentation/cards --------------------------------------------------------

def test_recommendation_card_enriches_only_from_record():
    card = build_recommendation_card({
        "store_id": 7, "recommendation": "PAUSE_INTERVENTION",
        "store_health_score": 41.0, "recovery_pct": -3.0, "confidence": 0.7,
        "forecast_status": "on_track", "days_remaining": 12,
        "requires_human_approval": True, "reason": "negative lift",
        "recommendation_id": "recommendation-abc",
    })
    assert card.store_id == 7
    assert card.requires_human_approval is True
    assert card.evidence_refs[0].ref_id == "recommendation-abc"
    assert card.evidence_refs[0].kind == "recommendation"


def test_digest_counts_only_gated_records():
    gated = {"store_id": 1, "recommendation": "ESCALATE",
             "requires_human_approval": True, "reason": "r",
             "recommendation_id": "recommendation-a"}
    plain = {"store_id": 2, "recommendation": "MONITOR",
             "requires_human_approval": False, "reason": "r"}
    digest = build_attention_digest([gated, plain])
    assert digest.total_pending == 1
    assert digest.cards[0].store_id == 1


def test_approval_preview_marks_gated_and_informational():
    gated = build_approval_preview(_gated_record())
    assert gated.action_class == "approval_required"
    plain = build_approval_preview({**_gated_record(),
                                    "recommendation": "MONITOR",
                                    "requires_human_approval": False})
    assert plain.action_class == "informational"


def test_approval_preview_surfaces_choice_architecture_on_gated_card():
    gated = build_approval_preview({
        "store_id": 7,
        "recommendation": "PAUSE_INTERVENTION",
        "reason": "negative lift",
        "recovery_pct": -4.0,
        "days_remaining": 10,
        "requires_human_approval": True,
        "recommendation_id": "recommendation-abc",
    })
    assert gated.default_action == "PAUSE_INTERVENTION"
    assert gated.fallback_action == "MONITOR"
    assert gated.risk == "reversible"
    assert gated.cost_of_inaction is not None
    assert "negative" in gated.cost_of_inaction


def test_approval_preview_omits_choice_architecture_on_informational():
    plain = build_approval_preview({
        "store_id": 7,
        "recommendation": "MONITOR",
        "reason": "on track",
        "recovery_pct": 1.0,
        "days_remaining": 10,
        "requires_human_approval": False,
        "recommendation_id": "recommendation-abc",
    })
    assert plain.default_action is None
    assert plain.fallback_action is None
    assert plain.risk is None
    assert plain.cost_of_inaction is None


def test_preview_card_schema_rejects_unknown_fields():
    from pydantic import ValidationError

    from presentation.cards import ApprovalPreviewCard
    with pytest.raises(ValidationError):
        ApprovalPreviewCard(
            store_id=7, recommendation="ESCALATE", action_class="approval_required",
            impact_statement="x", invented="field",
        )
