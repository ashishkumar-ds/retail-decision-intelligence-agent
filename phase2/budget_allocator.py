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


def allocate_budget(total_budget: float,
                    candidates: Sequence[Mapping[str, Any]]) -> BudgetAllocationPlan:
    """Allocate `total_budget` across `candidates` deterministically."""
    if not isinstance(total_budget, (int, float)) or isinstance(total_budget, bool):
        raise BudgetAllocatorError("total_budget must be a number")
    if total_budget <= 0:
        raise BudgetAllocatorError("total_budget must be positive")
    if not isinstance(candidates, (list, tuple)):
        raise BudgetAllocatorError("candidates must be a sequence of mappings")

    budget = Decimal(str(total_budget))
    scored: list[tuple[int, float]] = []  # (store_id, score)
    excluded: list[dict[str, Any]] = []

    for candidate in candidates:
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
        if not candidate["is_eligible"]:
            excluded.append({"store_id": sid, "reason": "not_eligible"})
            continue
        if candidate["evidence_state"] != "SUFFICIENT":
            excluded.append({"store_id": sid, "reason": f"evidence_state={candidate['evidence_state']}"})
            continue
        if lift <= 0:
            excluded.append({"store_id": sid, "reason": "non_positive_expected_lift"})
            continue
        score = float(lift) * float(confidence)
        scored.append((sid, score))

    # Highest score first, stable by store_id (deterministic ordering).
    scored.sort(key=lambda t: (-t[1], t[0]))

    # First pass: proportional to score; then enforce the per-store cap,
    # redistributing capped excess to uncapped stores proportionally.
    total_score = sum(s for _, s in scored)
    raw: dict[int, Decimal] = {}
    capped_flag: dict[int, bool] = {sid: False for sid, _ in scored}
    if total_score > 0:
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

    # Second pass: round to cents, drop dust below MIN_ALLOCATION.
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

    # Rounding can push cents over budget; trim the smallest allocation.
    if total_allocated > budget and allocations:
        overflow = total_allocated - budget
        smallest = min(allocations, key=lambda a: (a.allocation, a.store_id))
        trimmed = smallest.allocation - float(_cents(overflow))
        if trimmed >= float(MIN_ALLOCATION):
            allocations = [
                BudgetAllocation(a.store_id, a.score, round(trimmed, 2), a.capped, a.share_of_budget)
                if a.store_id == smallest.store_id else a for a in allocations
            ]
            total_allocated = sum((Decimal(str(a.allocation)) for a in allocations), Decimal(0))

    return BudgetAllocationPlan(
        total_budget=float(budget),
        allocated_budget=float(total_allocated),
        unallocated_budget=float(_cents(budget - total_allocated)),
        max_store_share=MAX_STORE_SHARE,
        allocations=tuple(sorted(allocations, key=lambda a: (-a.score, a.store_id))),
        excluded=tuple(excluded),
    )

