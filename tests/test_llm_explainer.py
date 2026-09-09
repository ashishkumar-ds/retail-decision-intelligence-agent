"""Offline tests for the LLM rephrase layer (no API key, no anthropic SDK).

Covers the fail-closed contract: the grounding gate rejects hallucinated
numbers / invented citations / empty drafts; a missing dependency or key
degrades to the template narrative; and the full success path works with a
stubbed anthropic module. The live LLM is intentionally never called here.

Hermeticity: these tests pin the anthropic provider path, so they scrub
ambient LLM_* env vars (LLM_PROVIDER / LLM_API_KEY / LLM_BASE_URL) — a
developer shell configured for an OpenAI-compatible endpoint must not flip
these tests onto the network.
"""
from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

import app.main as app_main
from app.config import llm_explanations_enabled
from rag.corpus import CorpusChunk
from rag.llm_explainer import (
    build_evidence_block,
    ground_llm_output,
    maybe_llm_narrative,
)


@pytest.fixture(autouse=True)
def _hermetic_llm_env(monkeypatch):
    """Scrub ambient provider selection so tests always take the stubbed path."""
    for var in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(var, raising=False)

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
TEMPLATE = (
    "Store 1's latest recommendation is CONTINUE with confidence 1.0 [rec:rec-abc]. "
    "It is driven by a health score of 100.0 (recovery 4.0 percent, 30 days remaining) [rec:rec-abc]."
)
CHUNK = CorpusChunk(
    chunk_id="src-golden123", title="Decision Intelligence", text="uplift methodology",
    source_type="methodology", source_path="rag/sources/methodology/x.md",
    license_note="in-repo", part=1,
)


def test_switch_defaults_off():
    assert llm_explanations_enabled() is False


# --- The grounding gate -------------------------------------------------------

def test_gate_passes_grounded_rephrase():
    text = ("Summary for store 1: the store is on track (health 100.0) with "
            "confidence 1.0 [rec:rec-abc]; a prior intervention measured +3.5 [event:outcome-1].")
    assert ground_llm_output(text, EVIDENCE, [CHUNK], []) == text


def test_gate_rejects_hallucinated_number():
    with pytest.raises(ValueError, match="numeric grounding"):
        ground_llm_output("Sales rose 12.7 percent [rec:rec-abc].", EVIDENCE, [CHUNK], [])


def test_gate_rejects_invented_citation():
    with pytest.raises(ValueError, match="citation guard"):
        ground_llm_output("On track with confidence 1.0 [event:event-xyz].", EVIDENCE, [CHUNK], [])


def test_gate_rejects_empty_output():
    with pytest.raises(ValueError, match="empty"):
        ground_llm_output("   ", EVIDENCE, [CHUNK], [])


def test_gate_allows_numbers_from_cited_chunk_text():
    # 900 appears only in the cited chunk text - allowed via Tier-2 grounding.
    chunk = CorpusChunk(
        chunk_id="src-golden123", title="Budgets", text="weekly budget 900 cap",
        source_type="methodology", source_path="x.md", license_note="in-repo", part=1,
    )
    text = f"Weekly budget capped at 900 [src:{chunk.chunk_id}]."
    assert ground_llm_output(text, EVIDENCE, [chunk], [(chunk, 1.0)]) == text


# --- Degradation paths ---------------------------------------------------------

def test_maybe_llm_disabled_serves_template():
    narrative, status = maybe_llm_narrative(1, "", TEMPLATE, EVIDENCE, [CHUNK], [], False)
    assert narrative == TEMPLATE
    assert "off" in status


def test_maybe_llm_enabled_without_sdk_degrades(monkeypatch):
    monkeypatch.setenv("LLM_EXPLANATIONS_ENABLED", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Ensure no stub anthropic module leaks into this path.
    monkeypatch.setitem(sys.modules, "anthropic", None)
    narrative, status = maybe_llm_narrative(1, "", TEMPLATE, EVIDENCE, [CHUNK], [], True)
    assert narrative == TEMPLATE  # fail-closed: template served, never an error
    assert status.startswith("llm-unavailable")


def test_maybe_llm_success_with_stubbed_sdk(monkeypatch):
    monkeypatch.setenv("LLM_EXPLANATIONS_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    grounded_text = ("Manager summary: store 1 is on track with health 100.0 and "
                     "confidence 1.0 [rec:rec-abc].")
    usage = types.SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0)
    response = types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=grounded_text)], usage=usage,
    )
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return response

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = lambda api_key: types.SimpleNamespace(messages=FakeMessages())
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

    narrative, status = maybe_llm_narrative(1, "", TEMPLATE, EVIDENCE, [CHUNK], [], True)
    assert status == "llm-grounded"
    assert narrative == grounded_text
    # Prompt discipline: static system prompt + one fenced user message.
    # The system prompt is sent as a single cache-controlled content block
    # (prompt-cache breakpoint; see rag/llm_explainer.build_static_system).
    system_block = captured["system"][0] if isinstance(captured["system"], list) else captured["system"]
    assert system_block["text"].startswith("You rephrase retail decision explanations")
    assert system_block["cache_control"] == {"type": "ephemeral"}
    assert "<evidence>" in captured["messages"][0]["content"]
    assert "TEMPLATE_NARRATIVE" in captured["messages"][0]["content"]


def test_maybe_llm_grounded_failure_serves_template(monkeypatch):
    monkeypatch.setenv("LLM_EXPLANATIONS_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    hallucinated = "Store 1 is amazing, sales jumped 42.7 percent."
    response = types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=hallucinated)],
        usage=types.SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0),
    )

    class FakeMessages:
        def create(self, **kwargs):
            return response

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = lambda api_key: types.SimpleNamespace(messages=FakeMessages())
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

    narrative, status = maybe_llm_narrative(1, "", TEMPLATE, EVIDENCE, [CHUNK], [], True)
    assert narrative == TEMPLATE
    assert status.startswith("llm-grounding-failed")


def test_evidence_block_is_fenced_and_complete():
    block = build_evidence_block(1, "why continue?", TEMPLATE, EVIDENCE, [(CHUNK, 1.0)])
    assert block.startswith("<evidence>") and block.endswith("</evidence>")
    assert TEMPLATE in block and "rec-abc" in block and "src-golden123" in block


# --- Free-provider path (openai_compat: Groq / Gemini / OpenRouter / Ollama) ----

def test_openai_compat_provider_success(monkeypatch):
    import rag.llm_explainer as llm_mod

    monkeypatch.setenv("LLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("LLM_API_KEY", "gsk-free-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    captured = {}

    def fake_post(url, headers, payload, timeout=60.0):
        captured.update(url=url, headers=headers, payload=payload)
        return {"choices": [{"message": {"content": "Store 1 is on track (health 100.0, confidence 1.0) [rec:rec-abc]."}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30}}

    monkeypatch.setattr(llm_mod, "_http_post_json", fake_post)
    narrative, status = maybe_llm_narrative(1, "", TEMPLATE, EVIDENCE, [CHUNK], [], True)
    assert status == "llm-grounded"
    assert narrative.startswith("Store 1 is on track")
    assert captured["url"].endswith("/chat/completions")
    assert captured["url"].startswith("https://api.groq.com/openai/v1")
    assert captured["headers"]["Authorization"] == "Bearer gsk-free-key"
    assert captured["payload"]["model"] == "llama-3.3-70b-versatile"
    assert captured["payload"]["messages"][0]["role"] == "system"


def test_openai_compat_provider_without_key_degrades(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compat")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    narrative, status = maybe_llm_narrative(1, "", TEMPLATE, EVIDENCE, [CHUNK], [], True)
    assert narrative == TEMPLATE
    assert status.startswith("llm-unavailable")


def test_openai_compat_grounded_failure_serves_template(monkeypatch):
    import rag.llm_explainer as llm_mod

    monkeypatch.setenv("LLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("LLM_API_KEY", "gsk-free-key")
    monkeypatch.setattr(
        llm_mod, "_http_post_json",
        lambda url, headers, payload, timeout=60.0: {
            "choices": [{"message": {"content": "Sales exploded by 77.7 percent!"}}],
            "usage": {},
        },
    )
    narrative, status = maybe_llm_narrative(1, "", TEMPLATE, EVIDENCE, [CHUNK], [], True)
    assert narrative == TEMPLATE
    assert status.startswith("llm-grounding-failed")


# --- Endpoint behavior ----------------------------------------------------------

def test_health_reports_llm_flag_default_off(monkeypatch):
    monkeypatch.delenv("LLM_EXPLANATIONS_ENABLED", raising=False)
    client = TestClient(app_main.app)
    assert client.get("/health").json()["features"]["llm_explanations"] is False


def test_why_degrades_to_template_when_llm_unavailable(monkeypatch):
    monkeypatch.setenv("LLM_EXPLANATIONS_ENABLED", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    client = TestClient(app_main.app)
    response = client.get("/why/999999")
    assert response.status_code == 200
    body = response.json()
    assert "guard" in body and body["guard"]["llm"].startswith("llm-unavailable")
