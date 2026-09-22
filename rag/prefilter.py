"""Pre-filter retrieved methodology chunks before the LLM context (rag/prefilter.py).

The skill recipe (classifier.dev "filter search results before reading them"):
the BM25 retriever hands back top-k methodology chunks; not all of them help
answer the reviewer's question. Sending all of them to the LLM is the context
cost we were trying to avoid, so this module classifies the chunks in ONE
batched call (keyless API, calibrated confidence) and keeps only the ones
worth putting in the evidence block.

Repo discipline (same as rag/llm_explainer.py and rag/advisor.py):

- NEVER on the decision path. This only runs when the LLM explanation layer
  is already enabled; it shapes the LLM's *context*, nothing else.
- Fail-OPEN here by design: the grounding gates downstream still validate
  against the FULL retrieved list, so a dropped chunk can only make the LLM
  omit a sentence, never break grounding. Any error (no network, 403, 429,
  malformed JSON) keeps every chunk with a logged status.
- Keep-biased (recall over precision - you never learn what you lost):
  a chunk is dropped only when the label is confidently "not relevant";
  low or null confidence always keeps it.
- Env-gated: RAG_PREFILTER_ENABLED=0 turns it into a no-op (default on).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Sequence

from .corpus import CorpusChunk

logger = logging.getLogger("retail_decision_agent.prefilter")

PREFILTER_ENABLED_ENV = "RAG_PREFILTER_ENABLED"
TIER_ENV = "RAG_PREFILTER_TIER"
# Drop gate, set from classifier.dev's measured calibration table rather than
# from the doc's example: their 0.7-0.9 confidence band is only ~85% accurate on
# easy (news-like) text and ~49% on hard (emotion-like) text, and methodology
# prose is closer to the hard end. A dropped chunk is invisible to the reader,
# so a silent false-drop costs more than carrying one extra chunk - only
# >= 0.9 answers may drop. Null confidence always keeps (smart-tier escalations
# return null confidence by design).
KEEP_CONFIDENCE_THRESHOLD = 0.9
# fast = Jev alone; smart = Jev plus a reasoning re-ask of answers under 0.7
# confidence (those come back with null confidence, so they are always kept).
_ALLOWED_TIERS = ("fast", "smart")
_LABELS = ["relevant", "not relevant"]
_USER_AGENT = "retail-decision-intelligence-agent/1.0 (rag-prefilter)"
_ENDPOINT = "https://classifier.dev"


def _classify_relevance(question: str, texts: Sequence[str]) -> list[dict[str, Any]]:
    """One batched classifier.dev call; returns results in input order."""
    tier = os.getenv(TIER_ENV, "fast").strip().lower()
    if tier not in _ALLOWED_TIERS:
        tier = "fast"  # an unknown tier is a 400 upstream; never send one
    body = json.dumps({
        "labels": list(_LABELS),
        "inputs": list(texts),
        "tier": tier,
        "instructions": (
            f'Relevant means the methodology chunk helps answer: {question}. '
            '"When in doubt, keep it."'
        ),
    }).encode()
    req = urllib.request.Request(
        _ENDPOINT,
        data=body,
        headers={"content-type": "application/json", "user-agent": _USER_AGENT},
    )
    # ponytail: sync urllib call with no retry/backoff - fine for the off-path
    # advisory layer's handful of chunks; upgrade path is the CLI's batched
    # streaming client if this ever moves to >1k chunks per request.
    return json.load(urllib.request.urlopen(req, timeout=10))["results"]


def filter_retrieved(question: str,
                     retrieved: Sequence[tuple[CorpusChunk, float]],
                     ) -> list[tuple[CorpusChunk, float]]:
    """Keep-biased relevance pre-filter over retrieved chunks. Never raises.

    Returns the (possibly shortened) list in original order. The dropped
    chunks never enter the LLM context.
    """
    if not retrieved:
        return list(retrieved)
    if os.getenv(PREFILTER_ENABLED_ENV, "1").strip().lower() in ("0", "false", "off"):
        return list(retrieved)
    # question is untrusted caller text; it goes into the classifier
    # instructions, so the same structural guard applies here.
    from .question_guard import sanitize_question
    question = sanitize_question(question)
    try:
        results = _classify_relevance(question, [c.text for c, _ in retrieved])
        if len(results) != len(retrieved):
            raise ValueError("classifier results do not match input count")
    except Exception as error:
        logger.warning("[PREFILTER UNAVAILABLE] keeping all %d chunks: %s: %s",
                       len(retrieved), type(error).__name__, error)
        return list(retrieved)

    kept: list[tuple[CorpusChunk, float]] = []
    for (chunk, score), result in zip(retrieved, results):
        label = result.get("label")
        confidence = result.get("confidence")
        unsure = label != "not relevant" or confidence is None \
            or confidence < KEEP_CONFIDENCE_THRESHOLD
        if unsure:
            kept.append((chunk, score))
        else:
            logger.debug("[PREFILTER] dropped [src:%s] (confidence %.2f)",
                         chunk.chunk_id, confidence)
    logger.info("[PREFILTER] kept %d/%d chunks for the LLM context",
                len(kept), len(retrieved))
    return kept