"""Feature switches for optional capabilities (enable_* pattern).

Follows the commerce-agents reference pattern: a capability the deployment
lacks (or wants off) is a config switch that removes its surface on every
path; a disabled capability refuses to serve (503) rather than silently
degrading. Flags are read at call time (not import time) so tests can flip
them per-test and a container restart picks up new env values.

All flags default to enabled - the full behavior is unchanged unless an
operator explicitly turns a capability off.
"""
from __future__ import annotations

import os

_FALSY = {"0", "false", "no", "off"}


def _flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() not in _FALSY


def rag_enabled() -> bool:
    """Whether the RAG knowledge layer (/why endpoint, corpus build) is active."""
    return _flag("RAG_ENABLED")


def phase2_enabled() -> bool:
    """Whether the Phase-2 intervention lifecycle API is active."""
    return _flag("PHASE2_ENABLED")


def actuals_feedback_enabled() -> bool:
    """Whether replay-mode outcome evaluation from the actuals feed is active."""
    return _flag("ACTUALS_FEEDBACK_ENABLED")


def llm_explanations_enabled() -> bool:
    """Whether /why may rephrase its grounded narrative with an LLM.

    Defaults to OFF (the only non-default flag): the deterministic template
    narrative is the safe baseline, and the LLM is a strictly additive,
    gated rephrasing of it. Requires the optional ``anthropic`` dependency
    and ``ANTHROPIC_API_KEY`` at call time; without them the endpoint
    degrades to the template (fail-closed), never errors.
    """
    return _flag("LLM_EXPLANATIONS_ENABLED", default="false")


def llm_advisory_enabled() -> bool:
    """Whether /advisory may draft LLM triage suggestions above the human gate.

    Defaults to OFF, like llm_explanations: the advisory is a strictly
    additive layer. It is structurally inert either way (auto_applied is
    always False, requires_human_approval always True); this flag only
    controls whether the LLM call happens at all. Without the anthropic
    dependency or API key the endpoint degrades to the engine's own
    recommendation (fail-closed), never errors.
    """
    return _flag("LLM_ADVISORY_ENABLED", default="false")


FEATURE_FLAGS = {
    "RAG_ENABLED": rag_enabled,
    "PHASE2_ENABLED": phase2_enabled,
    "ACTUALS_FEEDBACK_ENABLED": actuals_feedback_enabled,
    "LLM_EXPLANATIONS_ENABLED": llm_explanations_enabled,
    "LLM_ADVISORY_ENABLED": llm_advisory_enabled,
}


# --- Unified agent config (merchant-agent `MerchantAgentConfig` pattern) -----
#
# One frozen dataclass carrying every capability switch and safety limit, so
# a deployment snapshots one config object instead of scattering env reads.
# The module-level flag functions above stay for backward compatibility and
# remain the per-call read path (tests flip them per-test).
from dataclasses import dataclass  # noqa: E402


@dataclass(frozen=True)
class DecisionAgentConfig:
    """Immutable deployment configuration for the decision agent.

    Mirrors the commerce-agents merchant-agent's ``MerchantAgentConfig``:
    capability switches (``enable_*``), guardrail limits, and approval
    settings in one frozen object. ``load_config()`` derives it from the
    environment at snapshot time; consumers must not re-read env vars for
    anything carried here.
    """

    # Capability switches (same flags as FEATURE_FLAGS above)
    enable_rag: bool = True
    enable_phase2: bool = True
    enable_actuals_feedback: bool = True
    enable_llm_explanations: bool = False
    enable_llm_advisory: bool = False

    # Guardrail limits (merchant-agent "guardrail limits" slot)
    max_question_chars: int = 500            # /why question length cap
    max_sweep_workers: int = 8               # parallel store evaluation cap
    max_stores_per_portfolio_eval: int = 50  # portfolio evaluation cap

    # Approval settings
    approval_token_required: bool = True     # APPROVAL_AUTH_TOKEN fail-closed
    double_gate_decisions: bool = True       # re-verify guardrails at approve/reject time

    # Explanation settings
    llm_max_tokens: int = 700

    def as_feature_flags(self) -> dict[str, bool]:
        """Health-endpoint view of the switches."""
        return {
            "rag": self.enable_rag,
            "phase2": self.enable_phase2,
            "actuals_feedback": self.enable_actuals_feedback,
            "llm_explanations": self.enable_llm_explanations,
            "llm_advisory": self.enable_llm_advisory,
        }


def load_config() -> DecisionAgentConfig:
    """Snapshot the environment into a frozen DecisionAgentConfig."""
    return DecisionAgentConfig(
        enable_rag=rag_enabled(),
        enable_phase2=phase2_enabled(),
        enable_actuals_feedback=actuals_feedback_enabled(),
        enable_llm_explanations=llm_explanations_enabled(),
        enable_llm_advisory=llm_advisory_enabled(),
        approval_token_required=_flag("APPROVAL_AUTH_TOKEN_REQUIRED"),
    )
