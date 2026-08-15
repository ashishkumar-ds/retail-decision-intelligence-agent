"""Centralized human-approval policy for recommendations."""

APPROVAL_REQUIRED_RECOMMENDATIONS = frozenset(
    {"ESCALATE", "EXTEND_INTERVENTION", "NEEDS_REVIEW"}
)


def requires_human_approval(recommendation: str) -> bool:
    """Return whether a recommendation must be approved by a human."""
    return recommendation in APPROVAL_REQUIRED_RECOMMENDATIONS
