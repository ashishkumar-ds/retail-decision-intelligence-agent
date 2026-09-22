# Failure asymmetry: LLM layers fail closed, the pre-filter fails open

The LLM explanation and advisory layers fail **closed** — any failure serves
the deterministic template/engine output — because they replace content that
is otherwise grounded. The retrieval pre-filter (`rag/prefilter.py`) fails
**open** — any failure keeps every chunk — because it only shapes the LLM's
context: a dropped chunk can at worst make the LLM omit a sentence, while
grounding still validates against the full retrieved list. The asymmetry is
deliberate; an inconsistency flag here is not a bug to unify.

Related: precedent citations (`rag/precedents.py`) may be *cited* in advisory
notes but their numbers stay ungrounded — numbers trace only to the store's
own evidence. Quoting a precedent's measured uplift fails the grounding guard
by design.
