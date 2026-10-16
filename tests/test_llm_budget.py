"""Spend-guard tests for the off-path LLM (rag/llm_budget.py).

The guard exists because switching the LLM on behind ``/why`` and ``/advisory``
publishes a paid call to an unauthenticated URL. These tests pin the two
ceilings, their deliberately different failure modes, and the claim that a
spent budget is reported honestly rather than as "the flag is off".

No network and no API key: the LLM call itself is monkeypatched out, so what is
under test is the gate, not the model.
"""
from fastapi.testclient import TestClient

from rag import llm_budget
from rag.corpus import build_chunks
from rag.llm_explainer import maybe_llm_narrative


def _reset(monkeypatch, *, per_ip: str = "", daily: str = "") -> None:
    """Fresh counters and explicit knobs for every test."""
    llm_budget.reset()
    for name, value in (("LLM_PER_IP_HOURLY_LIMIT", per_ip),
                        ("LLM_DAILY_CALL_BUDGET", daily)):
        if value == "":
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


# --- Per-client sliding window -------------------------------------------------

def test_per_ip_window_allows_the_limit_then_refuses_with_a_retry_hint(monkeypatch):
    _reset(monkeypatch, per_ip="3")
    now = 1_000_000.0
    assert [llm_budget.ip_allowance("a", now=now + i)[0] for i in range(3)] == [True] * 3
    allowed, retry_after = llm_budget.ip_allowance("a", now=now + 3)
    assert allowed is False
    assert retry_after > 0  # a 429 without Retry-After tells the caller nothing
    # A refusal must not consume a slot, or a retry loop would push its own
    # window forward and stay blocked forever.
    assert llm_budget.ip_allowance("a", now=now + 4) == (False, retry_after - 1)
    # Other clients have their own window.
    assert llm_budget.ip_allowance("b", now=now + 4)[0] is True


def test_per_ip_window_frees_up_after_an_hour(monkeypatch):
    _reset(monkeypatch, per_ip="1")
    now = 2_000_000.0
    assert llm_budget.ip_allowance("a", now=now)[0] is True
    assert llm_budget.ip_allowance("a", now=now + 10)[0] is False
    assert llm_budget.ip_allowance("a", now=now + llm_budget.WINDOW_SECONDS + 1)[0] is True


def test_limits_of_zero_mean_unlimited(monkeypatch):
    _reset(monkeypatch, per_ip="0", daily="0")
    assert all(llm_budget.ip_allowance("a", now=1.0 + i)[0] for i in range(50))
    assert all(llm_budget.daily_allowance(now=1.0 + i)[0] for i in range(50))


def test_a_misconfigured_limit_falls_back_to_the_default_never_to_unlimited(monkeypatch):
    """A typo must not silently uncap the thing that guards the account."""
    _reset(monkeypatch, per_ip="twenty", daily="lots")
    assert llm_budget.per_ip_hourly_limit() == 20
    assert llm_budget.daily_call_budget() == 250


# --- Daily account budget ------------------------------------------------------

def test_daily_budget_stops_at_the_cap_and_resets_on_a_new_utc_day(monkeypatch):
    _reset(monkeypatch, daily="2")
    day_one = 1_700_000_000.0  # 2023-11-14T22:13:20Z
    assert llm_budget.daily_allowance(now=day_one) == (True, 1)
    assert llm_budget.daily_allowance(now=day_one) == (True, 2)
    assert llm_budget.daily_allowance(now=day_one) == (False, 2)
    assert llm_budget.daily_allowance(now=day_one + 86_400) == (True, 1)


# --- Caller identity -----------------------------------------------------------

def test_client_ip_uses_the_last_forwarded_entry_so_a_forgery_cannot_buy_quota():
    """A client can set X-Forwarded-For; the proxy appends what it observed."""
    assert llm_budget.client_ip("9.9.9.9, 203.0.113.7") == "203.0.113.7"
    assert llm_budget.client_ip("  203.0.113.7  ") == "203.0.113.7"
    assert llm_budget.client_ip(None, "10.0.0.1") == "10.0.0.1"
    assert llm_budget.client_ip("", "") == "unknown"


# --- The status string must not lie --------------------------------------------

def test_skip_reason_is_reported_instead_of_claiming_the_flag_is_off():
    narrative, status = maybe_llm_narrative(
        1, "", "Store 1 template [rec:recommendation-abc]", {}, [], [], False,
        skip_reason="daily LLM budget spent (250/250 calls today); template served",
    )
    assert narrative == "Store 1 template [rec:recommendation-abc]"
    assert "budget spent" in status and "LLM_EXPLANATIONS_ENABLED off" not in status


def test_default_status_is_unchanged_when_the_flag_is_the_reason():
    _, status = maybe_llm_narrative(1, "", "template", {}, [], [], False)
    assert status == "deterministic-template (LLM_EXPLANATIONS_ENABLED off)"


# --- Endpoints ------------------------------------------------------------------

def _rec(store_id: int = 317) -> dict:
    """A minimal recommendation record, same shape tests/test_rag_why.py uses.

    Duplicated rather than imported: ``tests/`` is not a package, so a
    cross-test import would only work under pytest's rootdir insertion.
    """
    return {
        "store_id": store_id, "recommendation": "CONTINUE", "confidence": 0.95,
        "reason": "on track", "store_health_score": 88.0, "recovery_pct": 9.0,
        "days_remaining": 30, "recommendation_id": "recommendation-abc123",
        "generated_at": "2026-08-30T10:00:00+00:00",
    }


def _grounded_client(monkeypatch, per_ip: str = "", daily: str = ""):
    """TestClient with the RAG layer stubbed and the LLM flag ON."""
    import app.main as app_main

    _reset(monkeypatch, per_ip=per_ip, daily=daily)
    monkeypatch.setenv("LLM_EXPLANATIONS_ENABLED", "true")
    monkeypatch.setattr(app_main, "read_log", lambda: [_rec()])

    class _FakeRegistry:
        def read_events(self):
            return []

    monkeypatch.setattr(app_main, "_phase2_registry", _FakeRegistry())
    monkeypatch.setattr(app_main, "load_corpus", lambda: build_chunks())
    return TestClient(app_main.app)


def test_why_answers_429_with_retry_after_once_the_client_is_over_its_window(monkeypatch):
    client = _grounded_client(monkeypatch, per_ip="2")
    # The rephrase itself is replaced: the gate is what this test is about.
    monkeypatch.setattr("rag.llm_explainer.rephrase",
                        lambda *a, **k: "no model call in this test")

    assert client.get("/why/317").status_code == 200
    assert client.get("/why/317").status_code == 200
    response = client.get("/why/317")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_why_degrades_honestly_when_the_daily_budget_is_spent(monkeypatch):
    client = _grounded_client(monkeypatch, daily="1")
    monkeypatch.setattr("rag.llm_explainer.rephrase",
                        lambda *a, **k: "no model call in this test")

    assert client.get("/why/317").status_code == 200           # consumes the budget
    body = client.get("/why/317").json()                       # over it now
    assert body["guard"]["llm"].startswith("deterministic-template (daily LLM budget spent")
    assert body["narrative"].startswith("Store 317")           # still a real answer


def test_client_ip_header_is_what_gets_limited(monkeypatch):
    """Two visitors behind different addresses do not share a window."""
    client = _grounded_client(monkeypatch, per_ip="1")
    monkeypatch.setattr("rag.llm_explainer.rephrase",
                        lambda *a, **k: "no model call in this test")

    assert client.get("/why/317", headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 200
    assert client.get("/why/317", headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 429
    assert client.get("/why/317", headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 200
