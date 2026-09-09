"""Grounded "why" explainer (Priority 4).

Assembles Tier-1 evidence for a store (recommendation log + Phase-2 registry
events), synthesizes a deterministic narrative that cites record IDs, and
enforces a numeric grounding guard: every number appearing in the narrative
must be traceable to the cited Tier-1 evidence or the cited Tier-2 corpus
chunks. Fail-closed: if the guard fails, the endpoint refuses to answer
rather than emit an ungrounded explanation.

No LLM is required: v1 explanations are template-synthesized from evidence.
The design leaves an LLM hook for later (it would only rephrase the same
cited facts, never introduce un-cited content).
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .corpus import CorpusChunk
from .llm_explainer import maybe_llm_narrative
from .retriever import BM25Retriever

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_CITATION_RE = re.compile(r"\[(rec|event|src):([^\]\s]+)\]")


# --- Tier-1 evidence assembly ------------------------------------------------

def _store_recommendations(store_id: int,
                           recommendation_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    recommendations = [
        dict(r) for r in recommendation_records
        if isinstance(r, Mapping) and r.get("store_id") == store_id
    ]
    recommendations.sort(key=lambda r: str(r.get("generated_at") or ""))
    return recommendations


def _store_events(store_id: int,
                  event_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    events = [
        dict(e) for e in event_records
        if isinstance(e, Mapping) and _event_store_id(e) == store_id
    ]
    events.sort(key=lambda e: str(e.get("occurred_at") or ""))
    return events


def _outcome_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    evaluate_events = [e for e in events if e.get("event_type") == "evaluate"]
    latest_evaluate = evaluate_events[-1] if evaluate_events else None
    outcome_payload = latest_evaluate.get("payload") if isinstance(latest_evaluate, Mapping) else None
    outcome_payload = outcome_payload if isinstance(outcome_payload, dict) else {}
    return {
        "evidence_state": outcome_payload.get("evidence_state"),
        "actual_uplift_pct": outcome_payload.get("actual_uplift_pct"),
        "target_assessment": outcome_payload.get("target_assessment"),
        "causal_evidence": outcome_payload.get("causal_evidence"),
        "outcome_id": outcome_payload.get("outcome_id"),
        "evaluated_at": latest_evaluate.get("occurred_at") if latest_evaluate else None,
    }


def gather_store_evidence(store_id: int,
                          recommendation_records: Sequence[Mapping[str, Any]],
                          event_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Collect Tier-1 evidence for one store from already-loaded records.

    Pure function over records (dicts), so callers can feed the recommendation
    log and the Phase-2 registry without I/O inside this module.
    """
    recommendations = _store_recommendations(store_id, recommendation_records)
    events = _store_events(store_id, event_records)
    decision_events = [
        e for e in events
        if e.get("event_type") in {"define", "approve", "reject", "start", "complete", "fail", "cancel"}
    ]

    return {
        "store_id": store_id,
        "latest_recommendation": recommendations[-1] if recommendations else None,
        "latest_decision": decision_events[-1] if decision_events else None,
        "intervention_count": len({e.get("intervention_id") for e in decision_events if e.get("intervention_id")}),
        "outcome": _outcome_summary(events),
        "event_count": len(events),
    }


def _event_store_id(event: Mapping[str, Any]) -> int | None:
    key = event.get("key")
    if isinstance(key, Mapping):
        return key.get("store_id")
    return None


# --- Narrative synthesis (deterministic templates) ---------------------------

def _fmt(value: Any, spec: str = "") -> str:
    return format(value, spec) if value is not None else "no value"


def _derive_causal_assessment(causal: Mapping[str, Any]) -> tuple[Any, Any, str]:
    """Deterministically derive (did, scale_eligible, assessment) from a causal
    evidence payload, mirroring causality.py thresholds; fail-closed."""
    did = causal.get("did_uplift_pct")
    scale_eligible = causal.get("scale_up_eligible")
    assessment = causal.get("assessment_state") or causal.get("evidence_state") or "unknown"
    # If assessment not precomputed but did exists, compute via same thresholds as causality.py
    if assessment in ("unknown", "SUFFICIENT", "INSUFFICIENT") and isinstance(did, (int, float)) and not isinstance(did, bool):
        from decision_engine.calibration import TARGET_UPLIFT_PCT
        if did >= TARGET_UPLIFT_PCT:
            return did, True if scale_eligible is None else scale_eligible, "CONFIRMED"
        if did >= 0:
            return did, False if scale_eligible is None else scale_eligible, "REVIEW_ZONE"
        return did, False if scale_eligible is None else scale_eligible, "REFUTED"
    if scale_eligible is None:
        # No did or precomputed flag — treat as blocked (fail-closed)
        scale_eligible = bool(causal.get("scale_up_eligible"))
    return did, scale_eligible, assessment


def _sufficient_outcome_sentences(
    evidence: Mapping[str, Any], outcome: Mapping[str, Any], citations: list[dict[str, str]],
) -> list[str]:
    outcome_ref = str(outcome.get("outcome_id") or "outcome")
    citations.append({"type": "event", "id": outcome_ref,
                      "detail": "latest outcome evaluation payload"})
    sentences = [
        (f"An evaluated intervention measured a raw lift of "
         f"{_fmt(outcome.get('actual_uplift_pct'), '+.1f')} percent versus its own baseline, "
         f"assessed as {outcome.get('target_assessment')} [event:{outcome_ref}].")
    ]
    causal = outcome.get("causal_evidence")
    if isinstance(causal, Mapping):
        did, scale_eligible, assessment = _derive_causal_assessment(causal)
        sentences.append(
            f"Matched-control DiD evidence is "
            f"{_fmt(did, '+.1f')} percent, so scale-up is "
            f"{'eligible' if scale_eligible else 'blocked'} "
            f"(assessment {assessment}) [event:{outcome_ref}]."
        )
    return sentences


def build_narrative(evidence: Mapping[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """Render the explanation from Tier-1 evidence. Returns (text, citations).

    Every interpolated value comes from the evidence itself, and each sentence
    carries the citation of the record it came from.
    """
    citations: list[dict[str, str]] = []
    store_id = evidence["store_id"]
    latest = evidence.get("latest_recommendation")
    if latest is None:
        return (
            f"Store {store_id} has no recommendation on record yet; there is "
            f"nothing to explain. Recommendations are produced by "
            f"POST /recommendations/run once the campaign audit log lists the store.",
            citations,
        )

    rec_id = str(latest.get("recommendation_id") or "unidentified")
    citations.append({"type": "rec", "id": rec_id,
                      "detail": "latest recommendation record"})
    sentences = [
        (f"Store {store_id}'s latest recommendation is {latest.get('recommendation')} "
         f"with confidence {latest.get('confidence')} [rec:{rec_id}].")
    ]
    if latest.get("store_health_score") is not None:
        sentences.append(
            f"It is driven by a health score of {latest.get('store_health_score')} "
            f"(recovery {latest.get('recovery_pct')} percent, "
            f"{latest.get('days_remaining')} days remaining) [rec:{rec_id}]."
        )

    outcome = evidence.get("outcome") or {}
    if outcome.get("evidence_state") == "SUFFICIENT":
        sentences.extend(_sufficient_outcome_sentences(evidence, outcome, citations))
    elif outcome.get("evidence_state"):
        sentences.append(
            f"Outcome evidence exists but is {outcome.get('evidence_state')}; "
            f"inconclusive evidence never changes a decision."
        )

    latest_decision = evidence.get("latest_decision")
    if latest_decision:
        event_ref = str(latest_decision.get("event_id"))
        citations.append({"type": "event", "id": event_ref,
                          "detail": "latest Phase 2 lifecycle event"})
        sentences.append(
            f"The registry shows {evidence.get('intervention_count')} intervention(s) "
            f"and the latest lifecycle event is '{latest_decision.get('event_type')}' "
            f"[event:{event_ref}]."
        )
    return " ".join(sentences), citations


# --- Numeric grounding guard (fail-closed) -----------------------------------

def numeric_grounding_check(narrative: str,
                            evidence: Mapping[str, Any],
                            cited_chunks: Sequence[CorpusChunk] = ()) -> list[float]:
    """Return narrative numbers that cannot be traced to the cited material.

    A number passes if it matches (within rounding tolerance) any numeric
    value found in the Tier-1 evidence or in the cited Tier-2 chunk texts.
    An empty result means the narrative is fully grounded.
    """
    def numbers_in(text: str) -> list[float]:
        # Strip citation markers like [rec:...] / [event:...] / [src:...] so
        # hyphenated IDs (e.g. event-abc123) don't inject false numbers like -2.
        stripped = _CITATION_RE.sub("", text)
        return [float(m) for m in _NUMBER_RE.findall(stripped)]

    allowed = set(numbers_in(_stringify_numbers(evidence)))
    for chunk in cited_chunks:
        allowed.update(numbers_in(chunk.text))
    violations = []
    for value in numbers_in(narrative):
        if not any(abs(value - a) <= 0.051 for a in allowed):
            violations.append(value)
    return violations


def _stringify_numbers(value: Any) -> str:
    """Flatten evidence to text so numeric variants (str/int/float) are captured."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return f"{value} {value:.1f} {value:+.1f} {value:.2f} {value:+.2f}"
    if isinstance(value, Mapping):
        return " ".join(_stringify_numbers(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_stringify_numbers(v) for v in value)
    return str(value)


def validate_citations(narrative: str, evidence: Mapping[str, Any],
                       corpus: Sequence[CorpusChunk]) -> list[str]:
    """Return citation IDs referenced in the narrative that do not exist."""
    corpus_ids = {c.chunk_id for c in corpus}
    rec_ids = {str((evidence.get("latest_recommendation") or {}).get("recommendation_id", ""))}
    outcome = evidence.get("outcome") or {}
    event_ids = {str(outcome.get("outcome_id") or "outcome")}
    latest_decision = evidence.get("latest_decision") or {}
    if latest_decision.get("event_id"):
        event_ids.add(str(latest_decision["event_id"]))
    unknown: list[str] = []
    for kind, ref in _CITATION_RE.findall(narrative):
        if kind == "src" and ref not in corpus_ids:
            unknown.append(f"src:{ref}")
        if kind == "rec" and ref not in rec_ids:
            unknown.append(f"rec:{ref}")
        if kind == "event" and ref not in event_ids:
            unknown.append(f"event:{ref}")
    return unknown


# --- Orchestration ------------------------------------------------------------

def explain_store(store_id: int,
                  recommendation_records: Sequence[Mapping[str, Any]],
                  event_records: Sequence[Mapping[str, Any]],
                  corpus: Sequence[CorpusChunk],
                  question: str = "",
                  llm_enabled: bool = False) -> dict[str, Any]:
    """Full grounded explanation for a store. Raises ValueError if a
    grounding guard fails (fail-closed contract).

    With ``llm_enabled`` the template narrative is additionally rephrased by
    an LLM and its output must pass the SAME grounding guards - any failure
    degrades back to the template (see rag/llm_explainer.py)."""
    evidence = gather_store_evidence(store_id, recommendation_records, event_records)
    narrative, citations = build_narrative(evidence)

    # Tier-2: retrieve methodology context for the question (or the decision).
    retrieved: list[tuple[CorpusChunk, float]] = []
    if corpus:
        base_query = str((evidence.get("latest_recommendation") or {}).get("recommendation", ""))
        query = (question or base_query +
                 " difference-in-differences matched controls decision intelligence uplift")
        retriever = BM25Retriever(list(corpus))
        retrieved = retriever.retrieve(query, k=3)

    violations = numeric_grounding_check(narrative, evidence, [c for c, _ in retrieved])
    if violations:
        raise ValueError(
            f"numeric grounding guard failed; untraceable numbers in narrative: {violations}"
        )
    unknown = validate_citations(narrative, evidence, list(corpus))
    if unknown:
        raise ValueError(f"citation guard failed; unknown citations: {unknown}")

    # LLM rephrase layer (additive, gated, fail-closed to the template).
    narrative, llm_guard = maybe_llm_narrative(
        store_id, question, narrative, evidence, list(corpus), retrieved, llm_enabled,
    )

    return {
        "store_id": store_id,
        "question": question,
        "narrative": narrative,
        "citations": citations,
        "methodology": [
            {"chunk_id": c.chunk_id, "title": c.title, "part": c.part,
             "score": score, "license_note": c.license_note}
            for c, score in retrieved
        ],
        "evidence": evidence,
        "guard": {"numeric_grounding": "passed", "citations": "passed",
                  "llm": llm_guard},
    }
