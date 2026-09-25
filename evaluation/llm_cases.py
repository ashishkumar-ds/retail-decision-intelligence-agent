"""Pinned cases for the off-path LLM layer: what the grounding gates must veto.

The decision path is pinned by golden cases (``evaluation/golden_cases.py``).
The off-path LLM layers need the same discipline for a different property: they
may never serve ungrounded content, and no provider - good or bad - may be able
to inject any. These cases pin, against one fixed evidence block, which drafts
are servable and which gate must veto the rest.

Same rule as the golden cases (ADR-0004): a deliberate change to a guard or to
this evidence block updates the pinned expectation here in the same commit, with
the rationale stated. The expectations are the SHIPPED behaviour of
``ground_llm_output`` + ``maybe_llm_narrative``, not an aspiration.

Run: ``python evaluation/llm_evals.py`` (also graded by
``tests/test_offpath_llm_evals.py``, so CI runs it either way).
"""
from __future__ import annotations

from dataclasses import dataclass

from rag.corpus import CorpusChunk

# --- The fixed evidence block every case is graded against --------------------
STORE_ID = 1
QUESTION = "why continue this intervention?"

EVIDENCE = {
    "store_id": 1,
    "latest_recommendation": {
        "recommendation_id": "rec-abc", "recommendation": "CONTINUE",
        "confidence": 1.0, "store_health_score": 100.0,
        "recovery_pct": 4.0, "days_remaining": 30,
    },
    "outcome": {"evidence_state": "SUFFICIENT", "outcome_id": "outcome-1",
                "actual_uplift_pct": 3.5},
    "intervention_count": 1,
}

TEMPLATE_NARRATIVE = (
    "Store 1's latest recommendation is CONTINUE with confidence 1.0 [rec:rec-abc]. "
    "It is driven by a health score of 100.0 (recovery 4.0 percent, 30 days remaining) "
    "[rec:rec-abc]."
)

# Tier-2 methodology chunk. Its TEXT is citable ground for numbers: the 56-day
# baseline appears in the text, not in the store's own evidence.
CORPUS = (
    CorpusChunk(
        chunk_id="src-golden123", title="Decision Intelligence",
        text=("Methodology: measured uplift compares 14 days of sales against the "
              "store's own 56-day baseline."),
        source_type="methodology", source_path="rag/sources/methodology/x.md",
        license_note="in-repo", part=1,
    ),
)

# The adversarial provider (the model-swap experiment) appends exactly this claim
# to every draft: an untraceable number. No provider may get it served.
ADVERSARIAL_CLAIM = " Sales rose 47 percent across 999 units in the last 14 days."
ADVERSARIAL_MARKERS = ("47", "999")


@dataclass(frozen=True)
class OffpathCase:
    """One pinned off-path LLM case.

    ``draft`` is what the stubbed provider returns; ``None`` means the provider
    raises (unavailability). ``expect_served`` is whether LLM content may reach
    the reader; ``expect_reason`` pins which gate must veto when it may not.
    """

    case_id: str
    description: str
    draft: str | None
    expect_served: bool
    expect_reason: str | None = None
    llm_enabled: bool = True
    tags: tuple[str, ...] = ()


CASES: tuple[OffpathCase, ...] = (
    OffpathCase(
        "grounded_faithful_rephrase",
        "A faithful rephrase of the template: same numbers, same citations.",
        "Store 1 is CONTINUE with confidence 1.0 [rec:rec-abc], on a health score of 100.0 "
        "(recovery 4.0 percent, 30 days remaining) [rec:rec-abc].",
        expect_served=True,
        tags=("served",),
    ),
    OffpathCase(
        "grounded_number_from_cited_chunk",
        "A number that appears in a cited Tier-2 chunk is groundable (recall).",
        "The 56-day baseline is the comparison the methodology uses for store 1 [src:src-golden123].",
        expect_served=True,
        tags=("served", "recall"),
    ),
    OffpathCase(
        "veto_untraceable_number",
        "An invented magnitude: 47 percent appears in no evidence and no chunk.",
        "Store 1's recovery is 47 percent [rec:rec-abc].",
        expect_served=False, expect_reason="numeric",
        tags=("veto", "hallucination"),
    ),
    OffpathCase(
        "veto_invented_citation",
        "A citation to a record that does not exist must never reach the reader.",
        "Store 1 is CONTINUE at confidence 1.0 [rec:rec-does-not-exist].",
        expect_served=False, expect_reason="citation",
        tags=("veto", "hallucination"),
    ),
    OffpathCase(
        "veto_unknown_engine_vocabulary",
        "A metric the engine does not emit looks like engine vocabulary (snake_case).",
        "Store 1's sales_uplift_pct is improving [rec:rec-abc].",
        expect_served=False, expect_reason="lexicon",
        tags=("veto", "hallucination"),
    ),
    OffpathCase(
        "veto_empty_draft",
        "An empty completion is a failed generation, not a short answer.",
        "   ",
        expect_served=False, expect_reason="empty",
        tags=("veto",),
    ),
    OffpathCase(
        "provider_unavailable_degrades",
        "Provider/parse failure degrades to the template (fail-closed, silent).",
        None,
        expect_served=False,
        tags=("degraded",),
    ),
    OffpathCase(
        "off_switch_serves_template",
        "With the layer switched off the provider is never called and nothing is recorded.",
        "Store 1 is CONTINUE with confidence 1.0 [rec:rec-abc].",
        expect_served=False, llm_enabled=False,
        tags=("degraded", "switch"),
    ),
)
