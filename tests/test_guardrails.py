"""Tests for the guardrails choice-architecture helpers.

These pin the policy that the approval surface is built on: reversibility
tier, the default/fallback option pair each gated action offers, and the
grounded (never invented) cost-of-inaction counterfactual. Pure functions -
offline, deterministic.
"""
from __future__ import annotations

from guardrails import (
    RISK_CAUTIOUS,
    RISK_IRREVERSIBLE,
    RISK_REVERSIBLE,
    choice_pair,
    cost_of_inaction,
    risk_of,
)

# --- risk_of: reversibility tier ----------------------------------------------

def test_reversible_actions_are_reversible():
    assert risk_of("PAUSE_INTERVENTION") == RISK_REVERSIBLE
    assert risk_of("TIMING_SHIFT") == RISK_REVERSIBLE
    assert risk_of("MONITOR") == RISK_REVERSIBLE


def test_cautious_actions_commit_this_cycle():
    assert risk_of("EXTEND_INTERVENTION") == RISK_CAUTIOUS
    assert risk_of("RETARGET_SEGMENT") == RISK_CAUTIOUS


def test_irreversible_actions_move_money_or_escalate():
    assert risk_of("REALLOCATE_BUDGET") == RISK_IRREVERSIBLE
    assert risk_of("ESCALATE") == RISK_IRREVERSIBLE
    assert risk_of("NEEDS_REVIEW") == RISK_IRREVERSIBLE


def test_unknown_recommendation_defaults_to_most_conservative():
    assert risk_of("WEIRD_ACTION") == RISK_IRREVERSIBLE


# --- choice_pair: the default/fallback option set -----------------------------

def test_gated_actions_give_a_choice_pair():
    pair = choice_pair("PAUSE_INTERVENTION")
    assert pair == ("PAUSE_INTERVENTION", "MONITOR")


def test_reallocate_offers_fallback_to_extend():
    assert choice_pair("REALLOCATE_BUDGET") == ("REALLOCATE_BUDGET", "EXTEND_INTERVENTION")


def test_ungated_actions_give_no_choice_pair():
    assert choice_pair("CONTINUE") is None
    assert choice_pair("MONITOR") is None


def test_choice_pair_only_defined_for_approval_gated_actions():
    assert all(choice_pair(r) is not None for r in (
        "ESCALATE", "EXTEND_INTERVENTION", "NEEDS_REVIEW", "PAUSE_INTERVENTION",
        "RETARGET_SEGMENT", "TIMING_SHIFT", "REALLOCATE_BUDGET"))


# --- cost_of_inaction: grounded counterfactual ---------------------------------

def test_cost_of_inaction_grounds_negative_recovery():
    text = cost_of_inaction({
        "recommendation": "PAUSE_INTERVENTION",
        "recovery_pct": -4.0,
        "days_remaining": 10,
    })
    assert text is not None
    assert "negative" in text
    assert "-4.0" in text
    assert "10 days" in text


def test_cost_of_inaction_grounds_flat_to_positive():
    g = cost_of_inaction({"recommendation": "EXTEND_INTERVENTION",
                          "recovery_pct": 1.5, "days_remaining": 20})
    assert "flat-to-positive" in g


def test_cost_of_inaction_returns_none_when_ungated():
    assert cost_of_inaction({"recommendation": "CONTINUE",
                             "recovery_pct": 1.0, "days_remaining": 10}) is None


def test_cost_of_inaction_returns_none_when_evidence_missing():
    assert cost_of_inaction({"recommendation": "PAUSE_INTERVENTION"}) is None
    assert cost_of_inaction({"recommendation": "PAUSE_INTERVENTION",
                             "recovery_pct": -1.0}) is None