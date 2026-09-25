#!/usr/bin/env python3
"""Off-path LLM evaluation: grounding vetoes + provider swap (fully offline).

Two questions, both answerable without an API key:

1. **Does the gate veto what it must?** Every pinned case replays through the
   real ``maybe_llm_narrative`` with a stubbed provider, and whatever gets
   served is re-checked against the real guards. A draft with an untraceable
   number, an invented citation or unknown engine vocabulary may never reach a
   reader - the hallucination veto, enforced as a hard gate rather than scored.

2. **Can a bad provider get anything served?** The provider-swap experiment:
   the same cases run again with an adversarial provider that appends an
   untraceable claim to every draft. Nothing may be served, and the *harness* -
   not the model - is what guarantees it. This is the offline form of the
   model-swap diagnostic: holding the harness fixed and varying the model must
   never change the safety outcome.

Reported: served rate, veto counts per gate, degradation rate, and the escape
count (ungrounded content that was served), which must be 0.

Hermetic by design: ``rephrase`` is stubbed, so no network and no API key. The
``LLM_*`` env vars are scrubbed so a shell configured for Groq/Anthropic cannot
turn this into a network test; live provider smoke lives in
``scripts/smoke_llm_live.py``.

Run: ``python evaluation/llm_evals.py [--quiet]`` -> exit 0 only if every pinned
expectation holds and no escape occurred. Each run appends to
``logs/eval_runs.jsonl`` with ``suite: offpath_llm``.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.llm_cases import (  # noqa: E402
    ADVERSARIAL_CLAIM,
    ADVERSARIAL_MARKERS,
    CASES,
    CORPUS,
    EVIDENCE,
    QUESTION,
    STORE_ID,
    TEMPLATE_NARRATIVE,
    OffpathCase,
)
from rag import llm_explainer as llm_mod  # noqa: E402
from rag import llm_telemetry as telemetry  # noqa: E402
from rag.explainer import numeric_grounding_check, validate_citations  # noqa: E402

EVAL_LOG_PATH = Path("logs/eval_runs.jsonl")
_RETRIEVED = [(CORPUS[0], 1.0)]


def _scrub_llm_env() -> None:
    """Hermetic: this suite must never reach a provider."""
    for var in [v for v in os.environ if v.startswith("LLM_")]:
        os.environ.pop(var, None)


def _provider(draft: str | None):
    """A stubbed provider: returns the draft, or raises (unavailability)."""
    def _stub(*_args, **_kwargs) -> str:
        if draft is None:
            raise RuntimeError("stubbed provider failure")
        return draft
    return _stub


def _attempt(case: OffpathCase, draft: str | None) -> tuple[str, str]:
    """Run one attempt through the real off-path pipeline with a stub provider."""
    with mock.patch.object(llm_mod, "rephrase", _provider(draft)):
        return llm_mod.maybe_llm_narrative(
            STORE_ID, QUESTION, TEMPLATE_NARRATIVE, EVIDENCE, list(CORPUS),
            _RETRIEVED, case.llm_enabled)


def _grounded(narrative: str) -> list[str]:
    """Whatever a reader sees must pass the same two guards. Returns violations."""
    problems: list[str] = []
    untraceable = numeric_grounding_check(narrative, EVIDENCE, [CORPUS[0]])
    if untraceable:
        problems.append(f"untraceable numbers {untraceable}")
    unknown = validate_citations(narrative, EVIDENCE, list(CORPUS))
    if unknown:
        problems.append(f"unknown citations {unknown}")
    return problems


def run_case(case: OffpathCase) -> dict:
    """Replay one pinned case; returns a graded result carrying the veto reason."""
    events_before = len(telemetry.read_events())
    narrative, status = _attempt(case, case.draft)
    events = telemetry.read_events()[events_before:]
    served = status == "llm-grounded"
    reason = events[0].get("reason") if events else None

    failures: list[str] = []
    if served != case.expect_served:
        failures.append(f"served={served}, expected {case.expect_served} (status: {status})")
    if case.expect_reason is not None and reason != case.expect_reason:
        failures.append(f"veto reason {reason!r}, expected {case.expect_reason!r}")
    if served and narrative != case.draft:
        failures.append("served narrative is not the grounded draft")
    if not served and narrative != TEMPLATE_NARRATIVE:
        failures.append("served narrative is not the deterministic template")
    failures += [f"ungrounded content served: {problem}" for problem in _grounded(narrative)]
    if case.llm_enabled and len(events) != 1:
        failures.append(f"expected exactly 1 telemetry event, got {len(events)}")
    if not case.llm_enabled and events:
        failures.append(f"a disabled layer recorded {len(events)} event(s)")

    return {
        "case_id": case.case_id,
        "passed": not failures,
        "failures": failures,
        "served": served,
        "outcome": events[0].get("outcome") if events else None,
        "reason": reason,
        "tags": list(case.tags),
    }


def run_provider_swap(case: OffpathCase) -> dict:
    """Same case, adversarial provider: no provider may get content served.

    ``applicable`` is False for the off-switch case (the provider is never
    called) and the provider-failure case (there is no draft to poison).
    """
    if not case.llm_enabled or case.draft is None:
        return {"case_id": case.case_id, "applicable": False, "passed": True, "failures": []}
    narrative, status = _attempt(case, (case.draft or "") + ADVERSARIAL_CLAIM)
    failures: list[str] = []
    if status == "llm-grounded":
        failures.append("an adversarial draft passed the gates (must be impossible)")
    leaked = [marker for marker in ADVERSARIAL_MARKERS if marker in narrative]
    if leaked:
        failures.append(f"the injected claim reached the reader: {leaked}")
    failures += [f"ungrounded content served: {problem}" for problem in _grounded(narrative)]
    return {"case_id": case.case_id, "applicable": True,
            "passed": not failures, "failures": failures}


def run_all(quiet: bool = False) -> dict:
    """Grade every pinned case plus the provider swap; print and log the rubric."""
    _scrub_llm_env()
    results = [run_case(case) for case in CASES]
    swaps = [run_provider_swap(case) for case in CASES]
    failed = [r for r in results if not r["passed"]]
    swap_failed = [s for s in swaps if not s["passed"]]
    vetoes: dict[str, int] = {}
    for result in results:
        if result["reason"]:
            vetoes[result["reason"]] = vetoes.get(result["reason"], 0) + 1
    served = sum(1 for r in results if r["served"])
    escapes = sum(1 for r in results
                  if any("ungrounded content served" in f for f in r["failures"]))
    escapes += sum(1 for s in swaps
                   if any("injected claim" in f or "ungrounded content served" in f
                          for f in s["failures"]))
    applicable = [s for s in swaps if s["applicable"]]

    if failed:
        print(f"Off-path LLM evals: {len(results) - len(failed)}/{len(results)} passed "
              f"({len(failed)} FAILED)")
        for result in failed:
            print(f"       {result['case_id']}: {'; '.join(result['failures'])}")
    else:
        print(f"Off-path LLM evals: {len(results)}/{len(results)} passed - every grounding "
              f"veto holds and nothing ungrounded was served.")
    if swap_failed:
        print(f"Provider swap: {len(applicable) - len(swap_failed)}/{len(applicable)} passed "
              f"({len(swap_failed)} FAILED)")
        for swap in swap_failed:
            print(f"       {swap['case_id']}: {'; '.join(swap['failures'])}")
    else:
        print(f"Provider swap: {len(applicable)}/{len(applicable)} passed - no provider "
              f"could get content past the harness.")
    if not quiet:
        detail = ", ".join(f"{reason} {count}" for reason, count in sorted(vetoes.items()))
        print(f"Rubric: served {served}, vetoed {sum(vetoes.values())} ({detail or 'none'}), "
              f"degraded {len(results) - served - sum(vetoes.values())}, escapes {escapes}")

    summary = {
        "suite": "offpath_llm", "total": len(results),
        "passed": len(results) - len(failed), "failed": len(failed),
        "served": served, "vetoes": vetoes, "escapes": escapes,
        "provider_swap_applied": len(applicable), "provider_swap_failed": len(swap_failed),
    }
    with EVAL_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "run_at": datetime.now(timezone.utc).isoformat(),
            "kind": "offpath_llm", **summary,
            "failed_case_ids": [r["case_id"] for r in failed],
        }) + "\n")
    return summary


if __name__ == "__main__":
    # Synthetic drafts must never enter the real telemetry trail (the run record
    # itself still lands in logs/eval_runs.jsonl, like the golden-case runs).
    with tempfile.TemporaryDirectory() as _tmp:
        os.environ["OFFPATH_LLM_LOG_PATH"] = str(Path(_tmp) / "offpath_llm.jsonl")
        _summary = run_all(quiet="--quiet" in sys.argv)
    sys.exit(1 if (_summary["failed"] or _summary["provider_swap_failed"]) else 0)

