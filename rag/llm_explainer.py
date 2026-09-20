"""LLM rephrasing layer for the grounded /why explanation (fail-closed).

The LLM may only REPHRASE the deterministic template narrative: every number
and citation it produces must pass the same grounding guards as the template
(``numeric_grounding_check`` / ``validate_citations``). Any failure - missing
dependency, missing API key, API error, empty answer, ungrounded number,
invented citation - degrades to the template narrative with a logged reason.
The LLM is never on the decision path; it cannot override, retune, or extend
any deterministic result.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Mapping, Sequence

from .corpus import CorpusChunk

logger = logging.getLogger("retail_decision_agent.llm_explainer")

LLM_MODEL_ENV = "LLM_MODEL"
DEFAULT_LLM_MODEL = "claude-sonnet-4-5"
# Reasoning models (e.g. Groq's openai/gpt-oss-*) spend completion tokens on
# hidden reasoning before the visible answer, so the default 700 can exhaust
# the budget mid-thought and yield empty content. Override with LLM_MAX_TOKENS.
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "700"))

# Provider selection: "anthropic" (default) or "openai_compat" for any
# OpenAI-compatible endpoint (Groq, Google Gemini, OpenRouter, Ollama, ...).
# Env vars:
#   LLM_PROVIDER      anthropic | openai_compat
#   LLM_MODEL         model name (provider-specific)
#   LLM_BASE_URL      openai_compat only: e.g. https://api.groq.com/openai/v1
#   LLM_API_KEY       openai_compat only (anthropic uses ANTHROPIC_API_KEY)
OPENAI_COMPAT_ENV = "LLM_PROVIDER_OPENAI_COMPAT"
PROVIDER_ENV = "LLM_PROVIDER"
BASE_URL_ENV = "LLM_BASE_URL"
API_KEY_ENV = "LLM_API_KEY"
DEFAULT_OPENAI_COMPAT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_OPENAI_COMPAT_MODEL = "llama-3.3-70b-versatile"

def build_static_system() -> str:
    """The static system prompt (commerce-agents `prompt.build_static_system`).

    Byte-stable across calls - the prompt-cache key. Grounding is ENFORCED by
    the gates in this module, not by this text: the prompt instructs, code
    guarantees. ``scripts/check.py`` pins this function's output so the
    prompt can never drift from the pinned artifact.
    """
    return (
        "You rephrase retail decision explanations for store managers.\n"
        "You will receive a TEMPLATE_NARRATIVE that is already fully grounded, "
        "plus the evidence records and methodology chunks behind it.\n"
        "Rewrite it as a clear, manager-friendly explanation.\n"
        "Rules:\n"
        "- Use only numbers that appear in the template or the evidence block.\n"
        "- Citations like [rec:id], [event:id], [src:id] may only reference IDs "
        "given in the template or the evidence block.\n"
        "- Only use metric names, decision states, and thresholds that appear "
        "in the template or the evidence block.\n"
        "- Never add recommendations, thresholds, or facts that are not present.\n"
        "- Do not soften or change the decision itself.\n"
        "- If you cannot ground a sentence, omit that sentence."
    )


# Backward-compatible module constant: derived from the builder, checked in CI
# by scripts/check.py ("derived artifacts cannot drift from their source").
SYSTEM_PROMPT = build_static_system()


# Grounding lexicon (commerce-agents merchant-agent `grounding.py` pattern):
# the closed vocabulary the LLM narrative may reference. The template is built
# from these, so the LLM rephrasing can never introduce a metric name, metric
# alias, or decision state that the deterministic engine does not define. This
# is the *claim* counterpart of the numeric grounding guard: numbers are
# checked by `numeric_grounding_check`, metric/decision vocabulary here.
DECISION_METRIC_NAMES = frozenset(
    {
        "store_health_score",
        "health_score",
        "recovery_pct",
        "recovery_percentage",
        "recovery_velocity",
        "velocity",
        "confidence",
        "days_remaining",
        "days_elapsed",
        "baseline_forecast",
        "current_forecast",
        "forecast_status",
        "did_uplift_pct",
        "actuals_coverage_days",
    }
)

DECISION_STATE_NAMES = frozenset(
    {
        "on_track",
        "behind",
        "at_risk",
        "no_data",
        "near_deadline",
        "meets_target",
        "review_zone",
        "negative",
        "inconclusive",
        "requires_human_approval",
    }
)


def build_lexicon(evidence: Mapping[str, Any]) -> frozenset[str]:
    """The set of vocabulary the narrative may use for this request.

    Static engine vocabulary plus whatever metric keys the evidence record
    actually carries, so new evidence fields are automatically groundable.
    """
    lexicon = set(DECISION_METRIC_NAMES) | set(DECISION_STATE_NAMES)
    lexicon.update(str(key) for key in evidence)
    return frozenset(lexicon)


def lexicon_check(text: str, evidence: Mapping[str, Any]) -> list[str]:
    """Fail-closed vocabulary gate (merchant-agent `grounding.py` analog).

    Flags decision-vocabulary-shaped tokens (snake_case metric names and
    machine-state names) that are neither in the lexicon nor plain English.
    Prose is never flagged; only tokens that *look like* engine vocabulary
    must be traceable to the engine.
    """
    lexicon = build_lexicon(evidence)
    violations: list[str] = []
    for token in re.findall(r"\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b", text):
        if token in lexicon:
            continue
        # English word pairs (e.g. "next_best") are outside the engine's
        # vocabulary but not engine vocabulary either; only flag tokens that
        # collide with engine metric/state naming (contain a decision-suffix
        # or a percent/score/state word).
        if re.search(r"(_pct|_score|_velocity|_status|_days|_forecast|_zone|_target|_risk|_track|_data|_deadline|_uplift|_confidence)$", token):
            violations.append(token)
    return sorted(set(violations))


class LLMUnavailableError(RuntimeError):
    """The LLM dependency/key is missing or the call failed."""


def build_evidence_block(store_id: int, question: str,
                         template_narrative: str, evidence: Mapping[str, Any],
                         retrieved: Sequence[tuple[CorpusChunk, float]]) -> str:
    """Per-request data, fenced - the static prompt never changes bytes."""
    from .question_guard import sanitize_question
    question = sanitize_question(question)
    lines = [
        "<evidence>",
        f"STORE_ID: {store_id}",
        f"QUESTION: {question or '(none)'}",
        "TEMPLATE_NARRATIVE:",
        template_narrative,
        "TIER1_EVIDENCE_JSON:",
        json.dumps(evidence, default=str, sort_keys=True),
        "METHODOLOGY_CHUNKS:",
    ]
    for chunk, _score in retrieved:
        lines.append(f"[src:{chunk.chunk_id}] {chunk.title}: {chunk.text}")
    lines.append("</evidence>")
    return "\n".join(lines)


# merchant-agent naming parity: the per-request fenced block is the dynamic
# context; the static prompt above is the system. Same function, two names,
# so the call sites read like the reference architecture.
build_dynamic_context = build_evidence_block


def _model_name() -> str:
    return os.getenv(LLM_MODEL_ENV, DEFAULT_LLM_MODEL)


def _provider() -> str:
    return os.getenv(PROVIDER_ENV, "anthropic").strip().lower()


def _http_post_json(url: str, headers: dict, payload: dict, timeout: float = 60.0) -> dict:
    """Thin JSON POST used by the openai_compat provider (seams for tests)."""
    import httpx

    response = httpx.post(url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _rephrase_openai_compat(block: str) -> str:
    """Rephrase via any OpenAI-compatible /chat/completions endpoint.

    Covers Groq, Google Gemini, OpenRouter, and local Ollama - all of which
    have free tiers (or are entirely free and local, in Ollama's case)."""
    base_url = os.getenv(BASE_URL_ENV, DEFAULT_OPENAI_COMPAT_BASE_URL).rstrip("/")
    api_key = os.getenv(API_KEY_ENV)
    if not api_key:
        raise LLMUnavailableError(f"{API_KEY_ENV} is not set for the openai_compat provider")
    model = os.getenv(LLM_MODEL_ENV, DEFAULT_OPENAI_COMPAT_MODEL)
    try:
        payload = _http_post_json(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload={
                "model": model,
                "max_tokens": LLM_MAX_TOKENS,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": block},
                ],
            },
        )
    except Exception as error:
        raise LLMUnavailableError(f"openai_compat call failed: {error}") from error
    try:
        text = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage") or {}
        logger.info("[LLM EXPLAIN] provider=openai_compat model=%s input=%s output=%s",
                    model, usage.get("prompt_tokens"), usage.get("completion_tokens"))
    except (KeyError, IndexError, TypeError) as error:
        raise LLMUnavailableError(f"openai_compat response malformed: {error}") from error
    return text or ""


def rephrase(store_id: int, question: str, template_narrative: str,
             evidence: Mapping[str, Any],
             retrieved: Sequence[tuple[CorpusChunk, float]]) -> str:
    """Rephrase via the configured provider. Raises LLMUnavailableError on
    any dependency/key/API failure."""
    from .prefilter import filter_retrieved
    retrieved = filter_retrieved(question, retrieved)
    block = build_evidence_block(store_id, question, template_narrative, evidence, retrieved)
    if _provider() == "openai_compat":
        return _rephrase_openai_compat(block)

    try:
        import anthropic  # optional dependency (pyproject [llm])
    except ImportError as error:
        raise LLMUnavailableError("anthropic package not installed (pip install '.[llm]')") from error
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMUnavailableError("ANTHROPIC_API_KEY is not set")

    try:
        client = anthropic.Anthropic(api_key=api_key)
        # Cache breakpoint on the static system prompt (merchant-agent
        # `rolling_conversation_cache` idea, adapted to a single-turn explainer):
        # the byte-stable system prompt is cached; the per-request fenced
        # block after it is never part of the cached prefix.
        response = client.messages.create(
            model=_model_name(),
            max_tokens=LLM_MAX_TOKENS,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": block}],
        )
    except Exception as error:
        raise LLMUnavailableError(f"anthropic API call failed: {error}") from error

    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    usage = getattr(response, "usage", None)
    logger.info(
        "[LLM EXPLAIN] model=%s input=%s output=%s cache_read=%s",
        _model_name(),
        getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None),
        getattr(usage, "cache_read_input_tokens", None),
    )
    return text


def ground_llm_output(text: str, evidence: Mapping[str, Any],
                      corpus: Sequence[CorpusChunk],
                      retrieved: Sequence[tuple[CorpusChunk, float]]) -> str:
    """The gate: same guards the template must pass, applied to the LLM draft.

    Raises ValueError (fail-closed) for empty output, untraceable numbers, or
    invented citations."""
    # Imported lazily: explainer imports this module, so a module-level
    # import of its guards here would be circular.
    from .explainer import numeric_grounding_check, validate_citations

    if not text or not text.strip():
        raise ValueError("LLM returned an empty narrative")
    violations = numeric_grounding_check(text, evidence, [c for c, _ in retrieved])
    if violations:
        raise ValueError(f"numeric grounding guard failed; untraceable numbers: {violations}")
    unknown = validate_citations(text, evidence, list(corpus))
    if unknown:
        raise ValueError(f"citation guard failed; unknown citations: {unknown}")
    lex_violations = lexicon_check(text, evidence)
    if lex_violations:
        raise ValueError(f"lexicon guard failed; unknown engine vocabulary: {lex_violations}")
    return text


def maybe_llm_narrative(store_id: int, question: str, template_narrative: str,
                        evidence: Mapping[str, Any], corpus: Sequence[CorpusChunk],
                        retrieved: Sequence[tuple[CorpusChunk, float]],
                        llm_enabled: bool) -> tuple[str, str]:
    """Return (narrative, guard_llm_status). Never raises: every failure mode
    degrades to the deterministic template."""
    if not llm_enabled:
        return template_narrative, "deterministic-template (LLM_EXPLANATIONS_ENABLED off)"
    try:
        draft = rephrase(store_id, question, template_narrative, evidence, retrieved)
        grounded = ground_llm_output(draft, evidence, corpus, retrieved)
        return grounded, "llm-grounded"
    except ValueError as error:
        logger.warning("[LLM GROUNDING FAILURE] store %s: %s - serving template", store_id, error)
        return template_narrative, f"llm-grounding-failed ({error}); template served"
    except Exception as error:
        logger.warning("[LLM EXPLAIN UNAVAILABLE] store %s: %s: %s - serving template",
                       store_id, type(error).__name__, error)
        return template_narrative, f"llm-unavailable ({type(error).__name__}); template served"
