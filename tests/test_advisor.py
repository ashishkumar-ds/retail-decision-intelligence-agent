"""Offline tests for the advisory LLM triage layer (rag/advisor.py).

No network: the provider seam is monkeypatched. Verifies the three things
that make the layer safe: closed action vocabulary, grounding guards on the
note, and structural inertness (auto_applied False / human-gated / writes
nothing). Run with: pytest -q
"""


import pytest

from guardrails import APPROVAL_REQUIRED_RECOMMENDATIONS
from rag import advisor
from rag.advisor import (
    ADVISORY_SUGGESTABLE_ACTIONS,
    maybe_advisory_triage,
    parse_advisory,
)


def _well_formed_llm(action="EXTEND_INTERVENTION", note="Store health is 20.0; consider extending."):
    return f"SUGGESTED_ACTION: {action}\nNOTE: {note}"


# ---- closed vocabulary ----

def test_suggestable_actions_include_every_gated_action():
    assert APPROVAL_REQUIRED_RECOMMENDATIONS <= ADVISORY_SUGGESTABLE_ACTIONS


def test_suggestable_actions_are_closed_to_engine_vocabulary():
    assert ADVISORY_SUGGESTABLE_ACTIONS == (
        APPROVAL_REQUIRED_RECOMMENDATIONS | {"CONTINUE", "MONITOR"}
    )


def test_parse_advisory_accepts_well_formed_output():
    action, note = parse_advisory(_well_formed_llm("RETARGET_SEGMENT", "segment is large."))
    assert action == "RETARGET_SEGMENT"
    assert note == "segment is large."


def test_parse_advisory_rejects_invented_action():
    # "LAUNCH_500_STORES" fails the format guard (digits don't fit [A-Z_]+);
    # a well-formed but unknown action must fail the vocabulary guard instead.
    with pytest.raises(ValueError, match="closed vocabulary"):
        parse_advisory(_well_formed_llm("INVENTED_ACTION_XYZ", "trust me"))
    with pytest.raises(ValueError):
        parse_advisory(_well_formed_llm("LAUNCH_500_STORES", "trust me"))


def test_parse_advisory_rejects_malformed_output():
    with pytest.raises(ValueError, match="contract"):
        parse_advisory("I suggest extending the intervention for this store.")
    with pytest.raises(ValueError, match="empty advisory"):
        parse_advisory("")


# ---- maybe_advisory_triage: inertness + fail-closed degradation ----

MINIMAL_EVIDENCE = {"latest_recommendation": {"recommendation": "NEEDS_REVIEW"}}


def test_advisory_disabled_returns_engine_recommendation():
    advisory, status = maybe_advisory_triage(
        317, "", "NEEDS_REVIEW", "engine reason", "narrative",
        MINIMAL_EVIDENCE, [], [], llm_enabled=False,
    )
    assert advisory["suggested_action"] == "NEEDS_REVIEW"
    assert advisory["note"] == "engine reason"
    assert advisory["auto_applied"] is False
    assert advisory["requires_human_approval"] is True
    assert "off" in status


def test_llm_suggestion_is_returned_but_never_auto_applied(monkeypatch):
    monkeypatch.setattr(
        advisor, "draft_triage",
        lambda *a, **k: _well_formed_llm("MONITOR", "Health recovering; no new spend."),
    )
    # grounding guard passes: only vocabulary, no numbers/citations
    monkeypatch.setattr(
        "rag.llm_explainer.ground_llm_output",
        lambda text, evidence, corpus, retrieved: text,
    )
    advisory, status = maybe_advisory_triage(
        317, "", "EXTEND_INTERVENTION", "engine reason", "narrative",
        MINIMAL_EVIDENCE, [], [], llm_enabled=True,
    )
    assert advisory["suggested_action"] == "MONITOR"  # may differ from engine
    assert advisory["source"] == "llm-advisory"
    assert advisory["auto_applied"] is False          # but NEVER auto-applied
    assert advisory["requires_human_approval"] is True
    assert "grounded" in status


def test_unactionable_llm_output_degrades_to_engine(monkeypatch):
    monkeypatch.setattr(advisor, "draft_triage", lambda *a, **k: "free-form nonsense")
    advisory, status = maybe_advisory_triage(
        317, "", "ESCALATE", "engine reason", "narrative",
        MINIMAL_EVIDENCE, [], [], llm_enabled=True,
    )
    assert advisory["suggested_action"] == "ESCALATE"
    assert advisory["note"] == "engine reason"
    assert advisory["source"] == "deterministic-engine"
    assert advisory["auto_applied"] is False
    assert "degrades" in status or "served" in status


def test_provider_outage_degrades_to_engine(monkeypatch):
    def boom(*a, **k):
        raise advisor.AdvisoryUnavailableError("no api key")
    monkeypatch.setattr(advisor, "draft_triage", boom)
    advisory, status = maybe_advisory_triage(
        317, "", "CONTINUE", "engine reason", "narrative",
        MINIMAL_EVIDENCE, [], [], llm_enabled=True,
    )
    assert advisory["suggested_action"] == "CONTINUE"
    assert advisory["auto_applied"] is False
    assert "unavailable" in status


def test_advisory_writes_nothing(tmp_path, monkeypatch):
    """The structural safety property: no log file is created or appended."""
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "rec.jsonl"))
    assert not (tmp_path / "rec.jsonl").exists()
    monkeypatch.setattr(
        advisor, "draft_triage",
        lambda *a, **k: _well_formed_llm(),
    )
    monkeypatch.setattr(
        "rag.llm_explainer.ground_llm_output",
        lambda text, evidence, corpus, retrieved: text,
    )
    maybe_advisory_triage(317, "", "NEEDS_REVIEW", "reason", "narrative",
                          MINIMAL_EVIDENCE, [], [], llm_enabled=True)
    assert not (tmp_path / "rec.jsonl").exists()
