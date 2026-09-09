"""Centralized human-approval policy for recommendations.

This is the single source of truth for what a recommendation requires and how
it should be presented to the human making the decision — a small *choice
architecture* on top of the decision engine.

Three roles live here (one home per rule):

- ``requires_human_approval``: whether an action needs a human at all
  (the blocker).
- ``risk_of``: how irreversible the action is. Reversibility differs per
  action — pausing is cheap and reversible, reallocating budget permanently
  moves money, and escalating pulls in a senior reviewer.
- ``choice_pair`` / ``cost_of_inaction``: the default-vs-fallback option set
  and the counterfactual the operator should weigh, so the human reacts to a
  well-formed decision instead of a blank prompt.

Together these are the "machine chooses the options, human picks" mechanism
the approval surface is built on (see presentation/cards.py).
"""
from __future__ import annotations

from typing import Any, Mapping

APPROVAL_REQUIRED_RECOMMENDATIONS = frozenset(
    {
        "ESCALATE",
        "EXTEND_INTERVENTION",
        "NEEDS_REVIEW",
        "PAUSE_INTERVENTION",
        # Diversified policy actions (Priority 2): each starts or moves money
        # for a new campaign play, so each is a human-approval-gated action.
        "RETARGET_SEGMENT",
        "TIMING_SHIFT",
        "REALLOCATE_BUDGET",
    }
)


def requires_human_approval(recommendation: str) -> bool:
    """Return whether a recommendation must be approved by a human."""
    return recommendation in APPROVAL_REQUIRED_RECOMMENDATIONS


# --- Choice architecture: risk, reversibility, cost of inaction --------------
#
# Tier semantics: the tier measures COMMITMENT, not process weight — how much
# spend or obligation the action creates that cannot be undone by simply
# deciding differently next cycle.
#
#   reversible   - can be undone with no lasting spend (pause, timing shift)
#   cautious     - spends budget THIS cycle but does not permanently commit
#                  the store (extend intervention, retarget a segment)
#   irreversible - permanently moves committed money (reallocate budget) or
#                  creates an obligation outside this system's control
#                  (escalate to a senior reviewer)
# Unknown recommendations default to the most conservative tier. Coverage of
# the emittable recommendation set is enforced by scripts/check.py — a new
# action must be tiered here deliberately, never silently defaulted.
RISK_REVERSIBLE = "reversible"
RISK_CAUTIOUS = "cautious"
RISK_IRREVERSIBLE = "irreversible"

RISK_TIER = {
    "PAUSE_INTERVENTION": RISK_REVERSIBLE,
    "TIMING_SHIFT": RISK_REVERSIBLE,
    "EXTEND_INTERVENTION": RISK_CAUTIOUS,
    "RETARGET_SEGMENT": RISK_CAUTIOUS,
    "REALLOCATE_BUDGET": RISK_IRREVERSIBLE,
    "ESCALATE": RISK_IRREVERSIBLE,
    "NEEDS_REVIEW": RISK_IRREVERSIBLE,
    "MONITOR": RISK_REVERSIBLE,
    "CONTINUE": RISK_REVERSIBLE,
}

# Default / fallback choice each gated action offers, so the human reacts to a
# well-formed option pair rather than a blank approval prompt.
DEFAULT_FALLBACK = {
    "PAUSE_INTERVENTION": ("PAUSE_INTERVENTION", "MONITOR"),
    "EXTEND_INTERVENTION": ("EXTEND_INTERVENTION", "MONITOR"),
    "ESCALATE": ("ESCALATE", "NEEDS_REVIEW"),
    "NEEDS_REVIEW": ("NEEDS_REVIEW", "MONITOR"),
    "RETARGET_SEGMENT": ("RETARGET_SEGMENT", "EXTEND_INTERVENTION"),
    "TIMING_SHIFT": ("TIMING_SHIFT", "EXTEND_INTERVENTION"),
    "REALLOCATE_BUDGET": ("REALLOCATE_BUDGET", "EXTEND_INTERVENTION"),
}


def risk_of(recommendation: str) -> str:
    """Reversibility tier for a recommendation (defaults conservative)."""
    return RISK_TIER.get(recommendation, RISK_IRREVERSIBLE)


def choice_pair(recommendation: str) -> tuple[str, str] | None:
    """Return ``(default_action, fallback_action)`` for a gated action.

    ``None`` for ungated recommendations - there is no choice to architect
    when no approval is required.
    """
    if not requires_human_approval(recommendation):
        return None
    return DEFAULT_FALLBACK.get(recommendation, (recommendation, "MONITOR"))


def cost_of_inaction(record: Mapping[str, Any]) -> str | None:
    """Ground the 'what happens if we do nothing' framing from recorded fields.

    Conservative and template-derived (never invented): it states the store's
    current trajectory plus the window, so the human has the counterfactual
    the approval surface needs. Returns ``None`` when the record cannot
    support a statement (missing recovery or window) - it never guesses.
    """
    if not requires_human_approval(record.get("recommendation", "")):
        return None
    recovery = record.get("recovery_pct")
    days = record.get("days_remaining")
    if recovery is None or days is None:
        return None
    sign = "negative" if isinstance(recovery, (int, float)) and recovery < 0 else "flat-to-positive"
    return (
        f"With no action, this store continues on a {sign} recovery "
        f"({recovery}%) with {days} days left in the window."
    )
