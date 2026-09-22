"""Tests for root-cause tagging (analytics/root_cause.py) - off-path analytics.

Network is never touched: the request body and parsing are asserted against a
fake transport, plus the egress-redaction guarantee that keeps store figures
inside the process."""
from __future__ import annotations

import json

import pytest

import analytics.root_cause as rc

RECORDS = [
    {"store_id": 317, "recommendation": "EXTEND_INTERVENTION",
     "reason": "Store 317 health score 62.0 (recovery 4.0 percent) - sales below window."},
    {"store_id": 12, "recommendation": "PAUSE_INTERVENTION",
     "reason": "Store 12 measured -12.5% observed sales lift vs its 56-day baseline (NEGATIVE).",
     "outcome_evidence": {"target_assessment": "NEGATIVE", "actual_uplift_pct": -12.5}},
]


def _fake_transport(monkeypatch, dimension_results):
    captured = {}

    class FakeResponse:
        def __init__(self, payload):
            self._raw = json.dumps(payload).encode()
        def read(self):
            return self._raw
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode())
        captured["url"] = request.full_url
        return FakeResponse({"results": dimension_results})
    monkeypatch.setattr(rc.urllib.request, "urlopen", fake_urlopen)
    return captured


def _dims(root_label="traffic decline", root_conf=0.95, driver="demand-side"):
    return {"dimensions": {
        "root_cause": {"label": root_label, "confidence": root_conf,
                       "scores": {root_label: root_conf}, "model": "jev-1.13.0"},
        "driver": {"label": driver, "confidence": 0.9, "scores": {}, "model": "jev-1.13.0"},
    }}


# --- egress redaction -----------------------------------------------------------

def test_redaction_strips_figures_and_store_ids():
    out = rc.redact_numbers("Store 317 health score 62.0 (recovery 4.0 percent), $1,250 at risk, -12.5% lift")
    assert "317" not in out and "62" not in out and "1,250" not in out and "12.5" not in out
    assert "store [id]" in out and "[n]" in out
    assert "health score" in out and "lift" in out  # semantics survive


def test_item_text_contains_semantics_not_magnitudes():
    text = rc._item_text(RECORDS[1])
    assert "RECOMMENDATION: PAUSE_INTERVENTION" in text
    assert "MEASURED_OUTCOME: NEGATIVE" in text
    assert "-12.5" not in text and "56" not in text and "Store 12" not in text


# --- request shape ---------------------------------------------------------------

def test_request_uses_dimensions_and_never_mixes_labels(monkeypatch):
    captured = _fake_transport(monkeypatch, [_dims(), _dims()])
    rc.classify_reasons(RECORDS)
    body = captured["body"]
    assert set(body["dimensions"]) == {"root_cause", "driver"}
    assert "labels" not in body and "multi" not in body  # contract: never combined
    assert body["tier"] == "fast"
    assert captured["url"].endswith("/v1/classify")
    # redaction holds for every item that egresses
    for item in body["items"]:
        stripped = item.replace("[n]", "").replace("[id]", "")
        assert not any(ch.isdigit() for ch in stripped)


def test_unknown_tier_env_is_coerced_to_fast(monkeypatch):
    captured = _fake_transport(monkeypatch, [_dims(), _dims()])
    monkeypatch.setenv(rc.TIER_ENV, "ultra")
    rc.classify_reasons(RECORDS)
    assert captured["body"]["tier"] == "fast"
    monkeypatch.setenv(rc.TIER_ENV, "smart")
    rc.classify_reasons(RECORDS)
    assert captured["body"]["tier"] == "smart"


# --- aggregation ------------------------------------------------------------------

def test_tags_are_aggregated_by_label_and_store(monkeypatch):
    _fake_transport(monkeypatch, [
        _dims("traffic decline"),
        _dims("pricing or promotion issue", driver="execution or process")])
    section = rc.tag_recommendations(RECORDS)
    assert section["status"] == "tagged (fast tier)"
    assert section["tagged"] == 2
    assert section["counts"] == {"traffic decline": 1, "pricing or promotion issue": 1}
    assert section["driver_counts"] == {"demand-side": 1, "execution or process": 1}
    assert section["by_store"][12]["root_cause"] == "pricing or promotion issue"
    assert section["by_store"][317]["root_cause_confidence"] == 0.95


def test_null_confidence_is_preserved_not_invented(monkeypatch):
    _fake_transport(monkeypatch, [_dims(root_conf=None), _dims(root_conf=None)])
    section = rc.tag_recommendations(RECORDS)
    assert section["by_store"][317]["root_cause_confidence"] is None
    assert section["counts"]  # a null confidence still yields its label


def test_unknown_label_from_a_changed_vocabulary_is_ignored(monkeypatch):
    _fake_transport(monkeypatch, [_dims("brand new label"), _dims("traffic decline")])
    section = rc.tag_recommendations(RECORDS)
    assert "brand new label" not in section["counts"]
    assert section["counts"] == {"traffic decline": 1}


# --- fail-open ---------------------------------------------------------------------

def test_network_failure_degrades_without_raising(monkeypatch):
    def boom(request, timeout=None):
        raise OSError("network down")
    monkeypatch.setattr(rc.urllib.request, "urlopen", boom)
    section = rc.tag_recommendations(RECORDS)
    assert section["status"] == "unavailable (OSError)"
    assert section["counts"] == {} and section["by_store"] == {}


def test_count_mismatch_degrades(monkeypatch):
    _fake_transport(monkeypatch, [_dims()])  # one result for two records
    assert rc.tag_recommendations(RECORDS)["status"].startswith("unavailable")


def test_records_without_reason_are_skipped(monkeypatch):
    captured = _fake_transport(monkeypatch, [_dims()])
    section = rc.tag_recommendations([{"store_id": 1}, {**RECORDS[0]}])
    assert len(captured["body"]["items"]) == 1
    assert section["tagged"] == 1


def test_no_usable_records_returns_empty_section():
    section = rc.tag_recommendations([])
    assert section["status"] == "no records to tag" and section["counts"] == {}


def test_item_cap_is_respected(monkeypatch):
    captured = _fake_transport(monkeypatch, [_dims()] * rc.MAX_ITEMS)
    many = [{**RECORDS[0], "store_id": i} for i in range(rc.MAX_ITEMS + 50)]
    rc.tag_recommendations(many)
    assert len(captured["body"]["items"]) == rc.MAX_ITEMS


# --- board rendering (no network: the section is supplied) -------------------------

def test_board_renders_the_section_when_supplied():
    from presentation.board import render_board_html
    html = render_board_html({
        "counts": {}, "buckets": {}, "total_stores": 0, "questions": [],
        "root_causes": {"status": "tagged (fast tier)", "tagged": 2,
                         "counts": {"traffic decline": 2},
                         "driver_counts": {"demand-side": 2}},
    })
    assert "Root causes across the estate" in html
    assert "traffic decline" in html
    assert "never a decision input" in html  # provenance is stated on the page


def test_board_omits_the_section_when_absent():
    from presentation.board import render_board_html
    html = render_board_html({"counts": {}, "buckets": {}, "total_stores": 0, "questions": []})
    assert "Root causes across the estate" not in html


def test_board_escapes_tag_labels():
    from presentation.board import render_board_html
    html = render_board_html({
        "counts": {}, "buckets": {}, "total_stores": 0, "questions": [],
        "root_causes": {"status": "tagged", "tagged": 1,
                         "counts": {"<script>alert(1)</script>": 1}, "driver_counts": {}},
    })
    assert "<script>" not in html and "&lt;script&gt;" in html


# --- endpoints (skip without fastapi) --------------------------------------------

def test_analytics_endpoint_requires_auth_and_returns_section(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import app.main as app_main
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("APPROVAL_AUTH_TOKEN", "tok")
    from memory.history import append_log
    append_log(dict(RECORDS[0]))
    monkeypatch.setattr(rc, "_post", lambda body, timeout=20.0: {
        "results": [{"dimensions": {
            "root_cause": {"label": "traffic decline", "confidence": 0.91},
            "driver": {"label": "demand-side", "confidence": 0.8}}}]})
    client = TestClient(app_main.app)
    assert client.get("/analytics/root-causes").status_code == 401
    assert client.get("/analytics/root-causes",
                      headers={"Authorization": "Bearer bad"}).status_code == 403
    ok = client.get("/analytics/root-causes", headers={"Authorization": "Bearer tok"})
    assert ok.status_code == 200
    assert ok.json()["counts"] == {"traffic decline": 1}


def test_board_does_not_egress_unless_opted_in(monkeypatch, tmp_path):
    """Privacy default: rendering the board must not call a third party."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import app.main as app_main
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("APPROVAL_AUTH_TOKEN", "tok")
    monkeypatch.delenv("ROOT_CAUSE_TAGGING_ENABLED", raising=False)
    from memory.history import append_log
    append_log(dict(RECORDS[0]))
    calls = []
    monkeypatch.setattr(rc, "_post", lambda body, timeout=20.0: calls.append(body) or {"results": []})
    client = TestClient(app_main.app)
    assert "root_causes" not in client.get("/board").json()
    assert calls == []  # no egress on a plain board render


def test_board_includes_section_when_opted_in(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import app.main as app_main
    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("ROOT_CAUSE_TAGGING_ENABLED", "true")
    from memory.history import append_log
    append_log(dict(RECORDS[0]))
    monkeypatch.setattr(rc, "_post", lambda body, timeout=20.0: {
        "results": [{"dimensions": {
            "root_cause": {"label": "pricing or promotion issue", "confidence": 0.88},
            "driver": {"label": "execution or process", "confidence": 0.7}}}]})
    client = TestClient(app_main.app)
    section = client.get("/board").json()["root_causes"]
    assert section["counts"] == {"pricing or promotion issue": 1}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
