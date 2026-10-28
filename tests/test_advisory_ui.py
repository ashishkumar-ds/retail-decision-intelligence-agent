"""Advisory ("Ask about this store") wiring contract (app/store/[id]/StoreDetail.tsx).

The advisory is the one LLM-backed surface on the stakeholder UI, so the
product's fail-closed ethos has to be visible in the client, not just the
server: the suggestion is labelled a suggestion, a degraded advisory says why
it degraded in sentences (never raw audit-log statuses), and the surface stays
read-only. These source-contract checks pin that so a refactor cannot quietly
turn the ask panel into a writer, a decider, or a raw-token printer.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETAIL = (ROOT / "app" / "store" / "[id]" / "StoreDetail.tsx").read_text(encoding="utf-8")


def test_the_ask_panel_stays_read_only():
    assert 'api(`/advisory/${encodeURIComponent(store)}?question=' in DETAIL, (
        "The ask panel must call GET /advisory/{store_id}?question=... through "
        "the same-origin proxy - that is the contract the backend serves."
    )
    assert "method: 'POST'" not in DETAIL and 'method: "POST"' not in DETAIL, (
        "The advisory surface must never write. The only POST on the "
        "stakeholder UI is the token-gated approve call on the board."
    )


def test_the_proxy_allowlists_the_advisory_read():
    # Found live, 2026-10-28: the panel shipped before the same-origin proxy
    # knew the path - the proxy's own 404 landed in the panel's generic error
    # and every ask died client-side, before reaching the API.
    proxy = (ROOT / "app" / "api" / "[...path]" / "route.ts").read_text(encoding="utf-8")
    assert "advisory" in proxy and "/^(why|advisory)\\/\\d+$/" in proxy, (
        "The /api proxy must forward GET advisory/{id} like why/{id}, or the "
        "ask panel 404s at the proxy and can never reach the API."
    )


def test_the_suggestion_is_labelled_a_suggestion():
    assert "Suggestion - not a decision, records nothing" in DETAIL, (
        "The advisory result must render under the permanent honesty line - "
        "the rag/advisor.py invariants (auto_applied false, writes nothing) "
        "made visible to the stakeholder."
    )


def test_the_status_is_translated_never_printed_raw():
    # advisory_status is audit-log wording ("deterministic (...)",
    # "llm-advisory (grounded)"); the surface renders advisoryStatusLine's
    # human sentences instead.
    assert "function advisoryStatusLine(" in DETAIL, (
        "advisory_status must go through the advisoryStatusLine translation."
    )
    assert "{askState.result.advisory_status}" not in DETAIL, (
        "The raw advisory_status string must not render on the page."
    )
    for known in ("grounding-failed", "unavailable", "budget", "no grounded evidence"):
        assert known in DETAIL, (
            f"advisoryStatusLine must translate the '{known}' outcome, not "
            "degrade it to a generic sentence."
        )


def test_the_question_is_capped_like_the_api():
    assert "maxLength={500}" in DETAIL, (
        "The input must cap the question at 500 characters, mirroring the "
        "API's MAX_QUESTION_CHARS guard (rag/question_guard.py)."
    )


def test_the_note_is_marker_stripped():
    assert "narrative(askState.result.advisory.note)" in DETAIL, (
        "The advisory note must have its [rec:/event:/src:] citation markers "
        "stripped before rendering, like every other prose on the page."
    )


def test_the_error_paths_answer_in_sentences():
    for status, fragment in ((429, "rate-limited"), (409, "grounded evidence"), (503, "switched off")):
        assert f"response.status === {status}" in DETAIL, (
            f"The {status} path must be handled explicitly."
        )
        assert fragment in DETAIL, (
            f"The {status} copy must say what actually happened ('{fragment}'), "
            "not surface the API's detail string."
        )