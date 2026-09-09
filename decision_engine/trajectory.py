"""Per-decision trajectory — what happened, step by step, for one store.

The illustrated-agents book (Grootendorst & Alammar) wraps every agent run in
a ``Trajectory`` object — ``initialize(task)``, then ``add(...)`` per step —
so what happened is a first-class, inspectable artifact instead of scattered
print output. This module adapts that pattern to a deterministic decision
pipeline: one ``DecisionTrajectory`` per store evaluation, one ``add`` per
pipeline stage, embedded in the recommendation record itself.

Two properties matter here that the book's conversational version does not
need:

- **Deterministic**: no wall-clock timestamps inside the trajectory (those
  live in the flat run log and the record's ``generated_at``), so two
  evaluations of the same evidence produce identical trajectories.
- **Embedded**: the trajectory travels inside the recommendation record, so
  ``/why``, the approval surface, and any future portal can cite the pipeline
  stages by name without re-deriving them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DecisionTrajectory:
    """The ordered pipeline stages for one store's evaluation."""

    store_id: int
    steps: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def start(cls, store_id: int) -> "DecisionTrajectory":
        """Initialize a trajectory for one store's evaluation."""
        return cls(store_id=store_id)

    def add(self, step: str, status: str, detail: str = "") -> "DecisionTrajectory":
        """Append one pipeline stage; chainable, order is run order."""
        self.steps.append({"step": step, "status": status, "detail": detail})
        return self

    def to_record(self) -> dict[str, Any]:
        """JSON-serializable record for embedding in the recommendation."""
        return {"store_id": self.store_id, "steps": list(self.steps)}

    def __len__(self) -> int:
        return len(self.steps)
