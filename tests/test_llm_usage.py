"""The provider-usage ledger (rag/llm_usage.py): counts only, off-path.

The provider adapters return text and stash usage here; the telemetry caller
folds it into the event. These tests pin the two things that make it safe to
put provider-reported numbers into an audit trail: garbage is dropped, not
coerced into a figure, and two in-flight requests never share a ledger.
"""
from __future__ import annotations

import threading

from rag import llm_usage


def test_drops_unparseable_fields():
    llm_usage.reset()
    llm_usage.set_last(tokens_in=None, tokens_out="", cache_read=True)
    assert llm_usage.last() == {}


def test_coerces_floats_and_rejects_negatives():
    llm_usage.set_last(tokens_in=12.9, tokens_out=3, cache_read=-5)
    assert llm_usage.last() == {"tokens_in": 12, "tokens_out": 3}
    llm_usage.reset()
    assert llm_usage.last() == {}


def test_ledger_is_thread_local():
    """FastAPI runs the LLM endpoints in a threadpool: no cross-talk."""
    llm_usage.reset()
    llm_usage.set_last(tokens_in=1)
    seen: dict[str, dict[str, int]] = {}

    def worker() -> None:
        seen["before"] = llm_usage.last()
        llm_usage.set_last(tokens_in=99)
        seen["after"] = llm_usage.last()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert seen["before"] == {}
    assert seen["after"] == {"tokens_in": 99}
    assert llm_usage.last() == {"tokens_in": 1}
