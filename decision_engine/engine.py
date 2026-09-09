"""The composed decision pipeline (TinyAgent-style constructor injection).

The illustrated-agents book composes its agent explicitly, one injected
component at a time::

    agent = TinyAgent(llm=llm, tools=tools, memory=memory, planner=react)

This module applies the same composition discipline to a deterministic
agent — the brain is code, not a model call::

    engine = DecisionEngine(router=route, planner=build_plan,
                            scorer=score_and_recommend,
                            verifier=verify_recommendation,
                            approval_gate=requires_human_approval)
    rec = engine.evaluate(signal, outcome_evidence=outcome_evidence)

Every component is injectable (tests can stub any stage; deployments could
swap one), the defaults are the production components, and ``evaluate`` runs
the same pipeline ``app/main.py`` has always run: route → plan → score →
verify → approval gate, with a ``DecisionTrajectory`` recording each stage
into the recommendation record itself.

The engine is PURE: no I/O, no clock, no file writes. ``app/main.py`` keeps
the I/O (forecast/audit adapters, the flat timestamped run log, persistence)
around this pure core.
"""
from __future__ import annotations

from typing import Any, Callable

from decision_engine.planner import build_plan, describe_plan
from decision_engine.router import route
from decision_engine.scorer import StoreSignal, no_data_recommendation, score_and_recommend
from decision_engine.trajectory import DecisionTrajectory
from decision_engine.verifier import verify_recommendation
from guardrails import requires_human_approval


class DecisionEngine:
    """The deterministic decision pipeline, composed once, injected anywhere."""

    def __init__(
        self,
        *,
        router: Callable[[StoreSignal], str] = route,
        planner: Callable[[str], list] = build_plan,
        scorer: Callable[..., dict] = score_and_recommend,
        verifier: Callable[[dict], dict] = verify_recommendation,
        approval_gate: Callable[[str], bool] = requires_human_approval,
    ):
        self._router = router
        self._planner = planner
        self._scorer = scorer
        self._verifier = verifier
        self._approval_gate = approval_gate

    def evaluate(self, signal: StoreSignal,
                 outcome_evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run the full pipeline for one store signal; returns the record.

        The pipeline order is fixed (route → plan → score → verify → gate) —
        a fixed-order flow enforces order in the backend, not in a prompt.
        """
        trajectory = DecisionTrajectory.start(signal.store_id)

        evaluation_route = self._router(signal)
        trajectory.add("route", "done", evaluation_route)

        plan = self._planner(evaluation_route)
        trajectory.add("plan", "done", "; ".join(describe_plan(plan)))

        # The plan drives execution, not just describes it.
        if "flag_for_review" in plan:
            rec = no_data_recommendation(signal)
            trajectory.add("score", "skipped", "no_data route - scoring skipped per plan")
        elif "score_and_recommend" in plan:
            causal_evidence = (outcome_evidence or {}).get("causal_evidence")
            rec = self._scorer(signal, outcome_evidence=outcome_evidence,
                               causal_evidence=causal_evidence)
            trajectory.add("score", "done", rec["recommendation"])
        else:
            # Defensive fallback - should be unreachable, but never a silent no-op.
            rec = no_data_recommendation(signal)
            trajectory.add("score", "warning", f"unrecognized plan {plan} - defaulted to review")

        verification = self._verifier(rec)
        trajectory.add("verify", "done" if verification["passed"] else "warning",
                       str(verification["details"]))

        # Guardrails is the single source of truth for approval requirements.
        rec["requires_human_approval"] = self._approval_gate(rec["recommendation"])
        rec["forecast_status"] = signal.forecast_status
        trajectory.add("approval_check", "done",
                       f"requires_approval={rec['requires_human_approval']}")

        rec["trajectory"] = trajectory.to_record()
        return rec