"""Board fallback: empty JSONL log + Supabase-backed pending store still serves.

Covers the diskless-Render case: /board, /board/view and /recommendations
classify the durable pending-approval records instead of answering empty.
Log wins when present (system of record); pending fills the gap.
"""
from fastapi.testclient import TestClient


def _seeded_pending(monkeypatch, tmp_path):
    from app import main

    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "empty.jsonl"))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "p.db"))
    main._pending_approvals.clear()
    rec = {
        "store_id": 7,
        "recommendation": "NEEDS_REVIEW",
        "reason": "No forecast signal available for store 7.",
        "store_health_score": 0.0,
        "recovery_pct": 0.0,
        "recovery_direction": "flat",
        "recovery_velocity": None,
        "days_remaining": 18,
        "requires_human_approval": True,
        "recommendation_id": "recommendation-test",
        "forecast_status": "NO_DATA",
        "forecast_signal_available": False,
        "generated_at": "2026-10-11T00:00:00+00:00",
    }
    main._pending_approvals[7] = rec
    return main


def test_board_falls_back_to_pending_store(monkeypatch, tmp_path):
    main = _seeded_pending(monkeypatch, tmp_path)
    client = TestClient(main.app)
    board = client.get("/board").json()
    assert board["total_stores"] == 1
    assert board["counts"]["insufficient_data"] == 1
    assert board["buckets"]["insufficient_data"][0]["store_id"] == 7


def test_board_view_and_recommendations_fall_back(monkeypatch, tmp_path):
    main = _seeded_pending(monkeypatch, tmp_path)
    client = TestClient(main.app)
    html = client.get("/board/view").text
    assert ">7<" in html or "7" in html
    recs = client.get("/recommendations").json()
    assert recs["total_stores"] == 1


def test_union_keeps_log_healthy_and_pending_gated(monkeypatch, tmp_path):
    """Log healthy CONTINUE + pending gated NEEDS_REVIEW union to 2, log wins ties."""
    import json

    from app import main

    monkeypatch.setenv("RECOMMENDATION_LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("PENDING_APPROVAL_STATE_PATH", str(tmp_path / "p.db"))
    main._pending_approvals.clear()
    (tmp_path / "log.jsonl").write_text(
        json.dumps({"store_id": 9, "recommendation": "MONITOR", "reason": "ok",
                    "store_health_score": 80.0, "recovery_pct": 0.0,
                    "requires_human_approval": False,
                    "recommendation_id": "r-log",
                    "forecast_status": "AVAILABLE"}) + "\n"
        + json.dumps({"store_id": 7, "recommendation": "CONTINUE", "reason": "stale",
                    "store_health_score": 50.0, "recovery_pct": 0.0,
                    "requires_human_approval": False,
                    "recommendation_id": "r-stale",
                    "forecast_status": "AVAILABLE"}) + "\n",
        encoding="utf-8",
    )
    main._pending_approvals[7] = {
        "store_id": 7, "recommendation": "NEEDS_REVIEW", "reason": "fresh",
        "store_health_score": 0.0, "recovery_pct": 0.0,
        "requires_human_approval": True, "recommendation_id": "r-fresh",
        "forecast_status": "NO_DATA", "generated_at": "2026-10-11T00:00:00+00:00",
    }
    from fastapi.testclient import TestClient
    board = TestClient(main.app).get("/board").json()
    assert board["total_stores"] == 2  # healthy 9 from log + gated 7 from pending
    assert board["buckets"]["working_well"][0]["store_id"] == 9


def test_log_wins_when_present(monkeypatch, tmp_path):
    import json

    main = _seeded_pending(monkeypatch, tmp_path)
    log_path = tmp_path / "empty.jsonl"
    log_path.write_text(
        json.dumps({"store_id": 9, "recommendation": "MONITOR", "reason": "ok",
                    "store_health_score": 80.0, "recovery_pct": 0.0,
                    "requires_human_approval": False,
                    "recommendation_id": "r-log",
                    "forecast_status": "AVAILABLE"}) + "\n",
        encoding="utf-8",
    )
    client = TestClient(main.app)
    board = client.get("/board").json()
    # union: healthy 9 from the log plus gated 7 from pending (log wins ties).
    assert board["total_stores"] == 2
    assert board["buckets"]["working_well"][0]["store_id"] == 9
    assert board["needs_decision"] == 1
