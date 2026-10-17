"""Last-call token usage for the off-path LLM providers (measurement side channel).

The provider adapters (``rag/llm_explainer.py``, ``rag/advisor.py``) return only
the generated text, because that is the seam the evaluators and the test suite
stub (``evaluation/llm_evals.py`` patches ``rephrase``, not the network). Token
usage is still worth keeping: it is the only cost and prompt-cache signal the
service has, and "log the cache read from day one" is the repo's own rule
(``docs/LLM_INTEGRATION_PATTERNS.md`` #11). So each adapter stashes the usage of
its most recent call here and the caller that owns the telemetry event folds it
in - the provider call sites stay stub-friendly.

Thread-local on purpose: the two LLM endpoints run in the FastAPI threadpool, so
two in-flight requests must never read each other's numbers. Every attempt resets
first (``maybe_llm_narrative`` / ``maybe_advisory_triage``), so a provider that
reports no usage can never inherit the previous call's figures.

This is off-path measurement, the same discipline as ``rag/llm_telemetry.py``:
it never influences a recommendation, an approval, or an execution, and a
provider that reports garbage simply contributes no numbers.
"""
from __future__ import annotations

import threading
from typing import Any

_local = threading.local()


def _as_count(value: Any) -> int | None:
    """Coerce a provider figure to a non-negative int, or drop it.

    Providers occasionally return ``None`` (a cache miss), ``""``, or a float;
    a census must never carry a ``True`` or a negative as if it were a token
    count. Anything unparseable is omitted rather than guessed.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return int(value)


def reset() -> None:
    """Forget the last call's usage (called at the start of every attempt)."""
    _local.value = None


def set_last(*, tokens_in: Any = None, tokens_out: Any = None,
             cache_read: Any = None) -> None:
    """Record the usage of the call just made; empty fields are dropped."""
    usage: dict[str, int] = {}
    for key, value in (("tokens_in", tokens_in), ("tokens_out", tokens_out),
                       ("cache_read", cache_read)):
        count = _as_count(value)
        if count is not None:
            usage[key] = count
    _local.value = usage or None


def last() -> dict[str, int]:
    """The current thread's last reported usage ({} when the provider gave none)."""
    value = getattr(_local, "value", None)
    return dict(value) if value else {}
