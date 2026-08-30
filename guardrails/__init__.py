"""Centralized human-approval policy for recommendations."""

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
