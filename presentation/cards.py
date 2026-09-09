"""Typed presentation payloads enriched from the system of record.

Every field on a card is either copied from a persisted record or computed
from persisted evidence; builders raise ``ValueError`` rather than guess.
Schemas follow the strict style of ``phase2/schemas.py`` (pydantic, runtime
validated) - these payloads are the contract with any future portal/UI.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from guardrails import (
    choice_pair,
    cost_of_inaction,
    requires_human_approval,
    risk_of,
)


class CardEvidenceRef(BaseModel):
    """A reference into the system of record; never an inline claim."""

    model_config = ConfigDict(extra="forbid")

    kind: str          # "recommendation" | "event" | "src" | ...
    ref_id: str


class RecommendationCard(BaseModel):
    """`present_metrics` analog: one store's decision summary."""

    model_config = ConfigDict(extra="forbid")

    store_id: int
    recommendation: str
    store_health_score: float | None = None
    recovery_pct: float | None = None
    confidence: float | None = None
    forecast_status: str | None = None
    days_remaining: int | None = None
    requires_human_approval: bool
    reason: str = ""
    recommendation_id: str | None = None
    evidence_refs: list[CardEvidenceRef] = Field(default_factory=list)
    limitations: list[dict[str, Any]] = Field(default_factory=list)


class AttentionDigestCard(BaseModel):
    """`present_digest` analog: the attention queue, ranked evidence attached."""

    model_config = ConfigDict(extra="forbid")

    generated_for: str = "retail-manager"
    total_pending: int
    cards: list[RecommendationCard] = Field(default_factory=list)


class ApprovalPreviewCard(BaseModel):
    """`present_change_preview` analog: what a gated action WOULD mean.

    Shown before the operator answers - the answer to this card is the
    approval surface, mirroring the merchant-agent's preview-before-apply.

    Beyond the impact statement it presents the *choice set* the operator is
    deciding between (``default_action`` vs ``fallback_action``), the
    reversibility of the action (``risk``) and the cost of not acting
    (``cost_of_inaction``) - so the human reacts to a well-formed decision
    rather than a blank prompt (the intelligent-choice-architecture pattern).
    """

    model_config = ConfigDict(extra="forbid")

    store_id: int
    recommendation: str
    action_class: str  # "approval_required" | "informational"
    rationale: str = ""
    impact_statement: str
    recommendation_id: str | None = None
    evidence_refs: list[CardEvidenceRef] = Field(default_factory=list)
    # Choice architecture (only populated for approval_required cards)
    default_action: str | None = None
    fallback_action: str | None = None
    risk: str | None = None
    cost_of_inaction: str | None = None


def build_recommendation_card(record: dict[str, Any]) -> RecommendationCard:
    """Enrich one persisted recommendation record into a decision card.

    Every value comes from the record; nothing is inferred. Evidence refs
    point at the persisted record so any number on a rendered card is
    traceable by ID.
    """
    rec_id = record.get("recommendation_id")
    refs = []
    if rec_id:
        refs.append(CardEvidenceRef(kind="recommendation", ref_id=str(rec_id)))
    return RecommendationCard(
        store_id=record["store_id"],
        recommendation=record.get("recommendation", ""),
        store_health_score=record.get("store_health_score"),
        recovery_pct=record.get("recovery_pct"),
        confidence=record.get("confidence"),
        forecast_status=record.get("forecast_status"),
        days_remaining=record.get("days_remaining"),
        requires_human_approval=bool(record.get("requires_human_approval")),
        reason=str(record.get("reason") or ""),
        recommendation_id=str(rec_id) if rec_id else None,
        evidence_refs=refs,
        limitations=[
            (lim if isinstance(lim, dict) else lim.to_record())
            for lim in record.get("data_limitations", [])
        ] if isinstance(record.get("data_limitations"), list) else [],
    )


def build_attention_digest(records: list[dict[str, Any]]) -> AttentionDigestCard:
    """Digest over pending approval-gated records (rank upstream, passed in)."""
    cards = [build_recommendation_card(r) for r in records
             if r.get("requires_human_approval")]
    return AttentionDigestCard(total_pending=len(cards), cards=cards)


def build_approval_preview(record: dict[str, Any]) -> ApprovalPreviewCard:
    """The pre-decision preview for one recommendation.

    The impact statement is template-grounded from record fields only - no
    LLM, no inference - so the operator previews exactly what the decision
    record supports. For approval-gated actions the card also surfaces the
    choice set (default vs fallback), the reversibility tier, and the cost of
    inaction, from the guardrails policy (guardrails/__init__.py) - so the
    human is choosing between well-formed options, not answering a blank
    approval prompt.
    """
    recommendation = record.get("recommendation", "")
    gated = requires_human_approval(recommendation)
    days = record.get("days_remaining")
    recovery = record.get("recovery_pct")
    if gated:
        impact = (
            f"Approval will authorise '{recommendation}' for store "
            f"{record.get('store_id')} based on the persisted evidence; this "
            f"decision is recorded in the decision ledger and cannot be applied "
            f"without it."
        )
    else:
        impact = f"Informational decision for store {record.get('store_id')}; no approval gate applies."
    if recovery is not None:
        impact += f" Current recovery: {recovery}%."
    if days is not None:
        impact += f" Days remaining in window: {days}."

    # Choice-architecture fields are only meaningful on a gated card.
    pair = choice_pair(recommendation) if gated else None
    rec_id = record.get("recommendation_id")
    refs = [CardEvidenceRef(kind="recommendation", ref_id=str(rec_id))] if rec_id else []

    return ApprovalPreviewCard(
        store_id=record["store_id"],
        recommendation=recommendation,
        action_class="approval_required" if gated else "informational",
        rationale=str(record.get("reason") or ""),
        impact_statement=impact,
        recommendation_id=str(rec_id) if rec_id else None,
        evidence_refs=refs,
        default_action=pair[0] if pair else None,
        fallback_action=pair[1] if pair else None,
        risk=risk_of(recommendation) if gated else None,
        cost_of_inaction=cost_of_inaction(record) if gated else None,
    )
