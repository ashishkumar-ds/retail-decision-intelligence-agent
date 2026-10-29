"""Question-document coverage contract (rag/sources/playbook/).

"Check documents based on questions": every preset ask on the store page must
retrieve document support that can actually answer it. Found 2026-10-29: the
corpus was a causal-methodology glossary, so all three presets retrieved DiD
chunks and the LLM answered from its prior dressed in irrelevant citations.
The playbook docs (do-nothing risk, pre-approval checklist, decision-change
policy) exist to close that gap; this test pins that they keep answering the
questions the UI asks.
"""
import re
from pathlib import Path

from rag.corpus import build_chunks
from rag.retriever import BM25Retriever

ROOT = Path(__file__).resolve().parents[1]

# The exact retrieval query the /advisory endpoint builds (app/main.py):
# question + this boost. Keep in sync; the assert below checks that.
BOOST = " difference-in-differences matched controls decision intelligence uplift"


def _presets_from_source() -> list[str]:
    detail = (ROOT / "app" / "store" / "[id]" / "StoreDetail.tsx").read_text(encoding="utf-8")
    block = re.search(r"const ASK_PRESETS = \[(.*?)\]", detail, re.S)
    assert block, "ASK_PRESETS vanished from StoreDetail.tsx"
    return re.findall(r"'([^']+)'", block.group(1))


def test_main_py_boost_string_matches():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert BOOST.strip() in main, (
        "The retrieval boost changed in app/main.py; update the copy here so "
        "the coverage test keeps auditing the query the advisory really sends."
    )


def test_every_preset_question_retrieves_playbook_support():
    presets = _presets_from_source()
    assert len(presets) >= 3, "The ask panel should keep at least three presets."
    retriever = BM25Retriever(list(build_chunks()))
    for question in presets:
        titles = [chunk.title for chunk, _ in retriever.retrieve(question + BOOST, k=3)]
        assert "playbook" in {c.source_type for c, _ in retriever.retrieve(question + BOOST, k=3)}, (
            f"Preset question {question!r} retrieves no playbook document in "
            f"its top 3 (got: {titles}). The LLM will answer from its prior "
            "instead of the corpus - add or fix a document that answers it."
        )
