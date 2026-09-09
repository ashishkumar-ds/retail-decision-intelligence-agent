"""Deterministic budget allocator for the store portfolio (Priority 2).

Allocates a fixed campaign budget across stores by expected lift x
confidence, subject to guardrails:

- Only eligible stores with SUFFICIENT outcome evidence and non-negative
  expected lift can receive budget (fail-closed: no evidence, no money).
- Per-store share is capped (MAX_STORE_SHARE) so no single store dominates.
- Score = expected_lift_pct x confidence, normalized to shares of the
  allocatable budget; rounding is to cents and never exceeds the budget.
- Stores below the minimum meaningful allocation are dropped and their
  budget stays unallocated (surfaced as `unallocated_budget`), rather than
  being silently spread.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Sequence

MAX_STORE_SHARE = 0.25   # no single store may take more than 25% of budget
MIN_ALLOCATION = Decimal("100.00")  # below this an allocation is not actionable

REQUIRED_FIELDS = ("store_id", "expected_lift_pct", "confidence", "is_eligible", "evidence_state")


class BudgetAllocatorError(ValueError):
    """Raised when a candidate list violates the allocator contract."""


@dataclass(frozen=True)
class BudgetAllocation:
    store_id: int
    score: float
    allocation: float
    capped: bool
    share_of_budget: float


@dataclass(frozen=True)
class BudgetAllocationPlan:
    total_budget: float
    allocated_budget: float
    unallocated_budget: float
    max_store_share: float
    allocations: tuple[BudgetAllocation, ...]
    excluded: tuple[dict[str, Any], ...]

    @property
    def methodology(self) -> dict[str, str]:
        return {
            "score": "expected_lift_pct * confidence (expected lift in % of baseline sales)",
            "eligibility": "is_eligible AND evidence_state == SUFFICIENT AND expected_lift_pct > 0",
            "cap": f"per-store allocation capped at {MAX_STORE_SHARE:.0%} of total budget",
            "min_allocation": f"allocations below ${MIN_ALLOCATION} are dropped (unallocated)",
            "determinism": "pure function of inputs; identical inputs give identical cents",
        }


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _validate_total_budget(total_budget: object) -> Decimal:
    if not isinstance(total_budget, (int, float)) or isinstance(total_budget, bool):
        raise BudgetAllocatorError("total_budget must be a number")
    if total_budget <= 0:
        raise BudgetAllocatorError("total_budget must be positive")
    return Decimal(str(total_budget))


def _validate_candidate(candidate: object) -> dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise BudgetAllocatorError("each candidate must be a mapping")
    missing = [f for f in REQUIRED_FIELDS if f not in candidate]
    if missing:
        raise BudgetAllocatorError(f"candidate missing fields: {missing}")
    sid = int(candidate["store_id"])
    lift = candidate["expected_lift_pct"]
    confidence = candidate["confidence"]
    if not isinstance(lift, (int, float)) or isinstance(lift, bool):
        raise BudgetAllocatorError(f"store {sid}: expected_lift_pct must be a number")
    if (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
            or not 0 <= confidence <= 1):
        raise BudgetAllocatorError(f"store {sid}: confidence must be a number in [0, 1]")
    return {"sid": sid, "lift": lift, "confidence": confidence}


def _exclusion_reason(candidate: Mapping[str, Any], lift: float) -> str | None:
    if not candidate["is_eligible"]:
        return "not_eligible"
    if candidate["evidence_state"] != "SUFFICIENT":
        return f"evidence_state={candidate['evidence_state']}"
    if lift <= 0:
        return "non_positive_expected_lift"
    return None


def _score_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[tuple[int, float]], list[dict[str, Any]]]:
    """Score eligible candidates. Returns (scored, excluded).

    scored is sorted highest score first, stable by store_id (deterministic).
    """
    scored: list[tuple[int, float]] = []
    excluded: list[dict[str, Any]] = []
    for candidate in candidates:
        validated = _validate_candidate(candidate)
        sid, lift = validated["sid"], validated["lift"]
        reason = _exclusion_reason(candidate, lift)
        if reason is not None:
            excluded.append({"store_id": sid, "reason": reason})
            continue
        scored.append((sid, float(lift) * float(validated["confidence"])))
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored, excluded


def _water_fill_proportions(
    scored: list[tuple[int, float]], budget: Decimal
) -> tuple[dict[int, Decimal], dict[int, bool]]:
    """Proportional-to-score split with per-store cap enforcement.

    Capped excess is redistributed to uncapped stores proportionally,
    iterating until no store exceeds the cap.
    """
    raw: dict[int, Decimal] = {}
    capped_flag: dict[int, bool] = {sid: False for sid, _ in scored}
    total_score = sum(s for _, s in scored)
    if total_score <= 0:
        return raw, capped_flag

    cap_money = budget * Decimal(str(MAX_STORE_SHARE))
    remaining_budget = budget
    active = {sid: score for sid, score in scored}
    for _ in range(len(scored) + 1):
        active_total = sum(active.values())
        if not active or active_total <= 0:
            break
        newly_capped = [
            sid for sid, score in active.items()
            if remaining_budget * Decimal(str(score)) / Decimal(str(active_total)) > cap_money
        ]
        if not newly_capped:
            for sid, score in active.items():
                raw[sid] = remaining_budget * Decimal(str(score)) / Decimal(str(active_total))
            break
        for sid in newly_capped:
            raw[sid] = cap_money
            capped_flag[sid] = True
            remaining_budget -= cap_money
            del active[sid]
    return raw, capped_flag


def _round_and_drop_dust(
    scored: list[tuple[int, float]],
    raw: dict[int, Decimal],
    capped_flag: dict[int, bool],
    budget: Decimal,
    excluded: list[dict[str, Any]],
) -> tuple[list[BudgetAllocation], Decimal]:
    """Round to cents; drop dust below MIN_ALLOCATION (unallocated)."""
    allocations: list[BudgetAllocation] = []
    total_allocated = Decimal(0)
    for sid, score in scored:
        amount = _cents(raw[sid])
        if amount < MIN_ALLOCATION:
            excluded.append({"store_id": sid, "reason": "below_min_allocation"})
            continue
        allocations.append(BudgetAllocation(
            store_id=sid, score=round(score, 4), allocation=float(amount),
            capped=bool(capped_flag.get(sid)),
            share_of_budget=float((amount / budget).quantize(Decimal("0.0001"))),
        ))
        total_allocated += amount
    return allocations, total_allocated


def _trim_overflow(
    allocations: list[BudgetAllocation], total_allocated: Decimal, budget: Decimal
) -> tuple[list[BudgetAllocation], Decimal]:
    """Rounding can push cents over budget; trim the smallest allocation."""
    if total_allocated <= budget or not allocations:
        return allocations, total_allocated
    overflow = total_allocated - budget
    smallest = min(allocations, key=lambda a: (a.allocation, a.store_id))
    trimmed = smallest.allocation - float(_cents(overflow))
    if trimmed < float(MIN_ALLOCATION):
        return allocations, total_allocated
    allocations = [
        BudgetAllocation(a.store_id, a.score, round(trimmed, 2), a.capped, a.share_of_budget)
        if a.store_id == smallest.store_id else a for a in allocations
    ]
    total_allocated = sum((Decimal(str(a.allocation)) for a in allocations), Decimal(0))
    return allocations, total_allocated


def allocate_budget(total_budget: float,
                    candidates: Sequence[Mapping[str, Any]]) -> BudgetAllocationPlan:
    """Allocate `total_budget` across `candidates` deterministically."""
    budget = _validate_total_budget(total_budget)
    if not isinstance(candidates, (list, tuple)):
        raise BudgetAllocatorError("candidates must be a sequence of mappings")

    scored, excluded = _score_candidates(candidates)
    raw, capped_flag = _water_fill_proportions(scored, budget)
    allocations, total_allocated = _round_and_drop_dust(
        scored, raw, capped_flag, budget, excluded,
    )
    allocations, total_allocated = _trim_overflow(allocations, total_allocated, budget)

    return BudgetAllocationPlan(
        total_budget=float(budget),
        allocated_budget=float(total_allocated),
        unallocated_budget=float(_cents(budget - total_allocated)),
        max_store_share=MAX_STORE_SHARE,
        allocations=tuple(sorted(allocations, key=lambda a: (-a.score, a.store_id))),
        excluded=tuple(excluded),
    )

