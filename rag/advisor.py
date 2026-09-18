"""Advisory LLM triage layer - ABOVE the human gate, never on the decision path.

The architecture deliberately separates two things:

  decision path   route -> plan -> score -> verify -> approval gate (code only)
  advisory layer  THIS module: an LLM drafts a triage suggestion for a human
                  reviewer, grounded in the deterministic engine's own evidence

Contract (same discipline as rag/llm_explainer.py, fail-closed):

- The LLM may ONLY choose a suggested action from the engine's closed
  recommendation vocabulary and write a grounded note. It cannot invent
  actions, thresholds, or numbers.
- Every suggestion is stamped ``auto_applied: False`` and
  ``requires_human_approval: True`` - structurally, in code, not by prompt
  instruction. Nothing in this module writes to memory/history.py or
  approvals/ledger.py; the advisory is evidence for the reviewer only.
- Any failure (missing dep, no API key, API error, malformed output, unknown
  action, ungrounded number/citation) degrades to the deterministic fallback:
  the engine's own recommendation and reason, with a logged status.

Endpoint surface: GET /advisory/{store_id} in app/main.py (read-only).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Mapping, Sequence

from guardrails import APPROVAL_REQUIRED_RECOMMENDATIONS

from .corpus import CorpusChunk

logger = logging.getLogger("retail_decision_agent.advisor")

# The ONLY actions the advisory may suggest: the engine's own recommendation
# vocabulary (the approval-gated set) plus the two benign outcomes. Every
# suggestion is human-gated anyway, so the worst case is a reviewer sees a
# suggestion and declines it.
ADVISORY_SUGGESTABLE_ACTIONS = frozenset(
    APPROVAL_REQUIRED_RECOMMENDATIONS | {"CONTINUE", "MONITOR"}
)

ADVISORY_MAX_TOKENS = 400
ACTION_LINE_RE = re.compile(r"^SUGGESTED_ACTION:\s*([A-Z_]+)\s*$", re.MULTILINE)
NOTE_LINE_RE = re.compile(r"^NOTE:\s*(.+)$", re.MULTILINE | re.DOTALL)


def build_advisory_system() -> str:
    """Static system prompt (byte-stable; the prompt-cache key).

    Grounding is ENFORCED by the gates in this module, not by this text:
    the prompt instructs, code guarantees.
    """
    return (
        "You are a retail-campaign triage assistant for a human reviewer.\n"
        "You receive the deterministic engine's evidence and recommendation for "
        "one store, plus its grounded narrative.\n"
        "Reply in EXACTLY this format:\n"
        "SUGGESTED_ACTION: <one action from the ALLOWED_ACTIONS list>\n"
        "NOTE: <2-4 sentences for the reviewer>\n"
        "Rules:\n"
        "- SUGGESTED_ACTION must be one of the actions in ALLOWED_ACTIONS.\n"
        "- Use only numbers that appear in the narrative or evidence block.\n"
        "- Citations like [rec:id], [event:id], [src:id] may only reference IDs "
        "given in the narrative or evidence block.\n"
        "- The suggestion is advisory only; a human decides. Do not claim it "
        "was applied.\n"
        "- If the evidence is inconclusive, suggest the most conservative "
        "allowed action and say why."
    )


# Backward-compatible module constant (same pattern as llm_explainer.SYSTEM_PROMPT).
ADVISORY_SYSTEM_PROMPT = build_advisory_system()


def _provider() -> str:
    return os.getenv("LLM_PROVIDER", "anthropic").strip().lower()


class AdvisoryUnavailableError(Exception):
    """Any dependency/key/API/malformed-output failure (fail-closed)."""


def _block(store_id: int, question: str, narrative: str,
           evidence: Mapping[str, Any]) -> str:
    latest = evidence.get("latest_recommendation") or {}
    current = latest.get("recommendation", "UNKNOWN")
    allowed = ", ".join(sorted(ADVISORY_SUGGESTABLE_ACTIONS))
    return "\n".join([
        f"STORE_ID: {store_id}",
        f"REVIEWER_QUESTION: {question or '(none)'}",
        f"ENGINE_RECOMMENDATION: {current}",
        f"ALLOWED_ACTIONS: {allowed}",
        "",
        "NARRATIVE_AND_EVIDENCE:",
        narrative,
        json.dumps(evidence, default=str, indent=1),
    ])


def _draft_openai_compat(block: str) -> str:
    from .llm_explainer import (
        API_KEY_ENV,
        BASE_URL_ENV,
        DEFAULT_OPENAI_COMPAT_BASE_URL,
        DEFAULT_OPENAI_COMPAT_MODEL,
        LLM_MODEL_ENV,
        _http_post_json,
    )
    base_url = os.getenv(BASE_URL_ENV, DEFAULT_OPENAI_COMPAT_BASE_URL).rstrip("/")
    api_key = os.getenv(API_KEY_ENV)
    if not api_key:
        raise AdvisoryUnavailableError(f"{API_KEY_ENV} is not set for the openai_compat provider")
    model = os.getenv(LLM_MODEL_ENV, DEFAULT_OPENAI_COMPAT_MODEL)
    try:
        payload = _http_post_json(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload={
                "model": model,
                "max_tokens": ADVISORY_MAX_TOKENS,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": ADVISORY_SYSTEM_PROMPT},
                    {"role": "user", "content": block},
                ],
            },
        )
    except Exception as error:
        raise AdvisoryUnavailableError(f"openai_compat call failed: {error}") from error
    try:
        return payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as error:
        raise AdvisoryUnavailableError(f"openai_compat response malformed: {error}") from error


def draft_triage(store_id: int, question: str, narrative: str,
                 evidence: Mapping[str, Any]) -> str:
    """Call the configured provider; raises AdvisoryUnavailableError on failure."""
    block = _block(store_id, question, narrative, evidence)
    if _provider() == "openai_compat":
        return _draft_openai_compat(block)

    try:
        import anthropic  # optional dependency (pyproject [llm])
    except ImportError as error:
        raise AdvisoryUnavailableError(
            "anthropic package not installed (pip install '.[llm]')") from error
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise AdvisoryUnavailableError("ANTHROPIC_API_KEY is not set")

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=os.getenv("LLM_MODEL", "claude-sonnet-4-5"),
            max_tokens=ADVISORY_MAX_TOKENS,
            system=[{
                "type": "text",
                "text": ADVISORY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": block}],
        )
    except Exception as error:
        raise AdvisoryUnavailableError(f"anthropic API call failed: {error}") from error

    return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")


def parse_advisory(text: str) -> tuple[str, str]:
    """Parse the rigid output contract; raises ValueError when malformed or
    when the suggested action is outside the engine's closed vocabulary."""
    if not text or not text.strip():
        raise ValueError("LLM returned an empty advisory")
    action_match = ACTION_LINE_RE.search(text)
    note_match = NOTE_LINE_RE.search(text)
    if not action_match or not note_match:
        raise ValueError("advisory output does not follow the SUGGESTED_ACTION/NOTE contract")
    action = action_match.group(1).strip()
    note = note_match.group(1).strip()
    if action not in ADVISORY_SUGGESTABLE_ACTIONS:
        raise ValueError(f"suggested action {action!r} is outside the engine's closed vocabulary")
    if not note:
        raise ValueError("advisory NOTE is empty")
    return action, note


def maybe_advisory_triage(store_id: int, question: str, current_recommendation: str,
                          fallback_note: str, narrative: str,
                          evidence: Mapping[str, Any], corpus: Sequence[CorpusChunk],
                          retrieved: Sequence[tuple[CorpusChunk, float]],
                          llm_enabled: bool) -> tuple[dict[str, Any], str]:
    """Return (advisory, status). Never raises; every failure degrades to the
    deterministic fallback (the engine's own recommendation and reason).

    The advisory dict is structurally inert: auto_applied is always False,
    requires_human_approval is always True, and nothing here writes to the
    recommendation log or the approvals ledger.
    """
    fallback = {
        "suggested_action": current_recommendation,
        "note": fallback_note,
        "source": "deterministic-engine",
        "auto_applied": False,
        "requires_human_approval": True,
    }
    if not llm_enabled:
        return fallback, "deterministic (LLM_ADVISORY_ENABLED off)"
    try:
        text = draft_triage(store_id, question, narrative, evidence)
        action, note = parse_advisory(text)
        # Same grounding guards the /why narrative must pass, applied to the
        # advisory note: untraceable numbers or invented citations fail closed.
        from .llm_explainer import ground_llm_output
        grounded_note = ground_llm_output(note, evidence, list(corpus), list(retrieved))
        return {
            "suggested_action": action,
            "note": grounded_note,
            "source": "llm-advisory",
            "auto_applied": False,
            "requires_human_approval": True,
        }, "llm-advisory (grounded)"
    except ValueError as error:
        logger.warning("[ADVISORY GROUNDING FAILURE] store %s: %s", store_id, error)
        return fallback, f"advisory-grounding-failed ({error}); engine recommendation served"
    except Exception as error:
        logger.warning("[ADVISORY UNAVAILABLE] store %s: %s: %s",
                       store_id, type(error).__name__, error)
        return fallback, f"advisory-unavailable ({type(error).__name__}); engine recommendation served"
