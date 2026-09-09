"""Presentation layer (merchant-agent `tools/presentation.py` pattern).

Typed, validated payload schemas for manager-facing "cards". The model (or
any narrative surface) never fills in numbers itself: a card schema carries
only references, and the builder functions here enrich it from the system of
record - the append-only recommendation log, the Phase 2 registry, and the
actuals feed - before it is served.

Three cards mirror the merchant-agent's built-in presentation tools:

- ``present_metrics``   -> :class:`RecommendationCard`   (the decision summary)
- ``present_digest``    -> :class:`AttentionDigestCard` (the attention queue)
- ``present_change_preview`` -> :class:`ApprovalPreviewCard`
  (what an approval-gated action would do, BEFORE the human decides)
"""
from presentation.cards import (  # noqa: F401
    ApprovalPreviewCard,
    AttentionDigestCard,
    RecommendationCard,
    build_approval_preview,
    build_attention_digest,
    build_recommendation_card,
)

__all__ = [
    "RecommendationCard",
    "AttentionDigestCard",
    "ApprovalPreviewCard",
    "build_recommendation_card",
    "build_attention_digest",
    "build_approval_preview",
]
