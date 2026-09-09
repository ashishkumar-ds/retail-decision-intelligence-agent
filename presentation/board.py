"""Executive status board — the stakeholder surface.

Answers, in one deterministic view, the three questions a retail manager
actually asks (the product goal of this project):

1. Which stores are RECOVERING under the campaign?
2. Which stores NEED INTERVENTION (and what should happen)?
3. Where is the campaign WORKING WELL — and for the rest, WHY is it not
   working?

Classification is a pure function over the persisted recommendation records
(``memory/history.py``) with first-match-wins rules, so the board is
recomputable and auditable exactly like the decision core. Every entry cites
its ``recommendation_id``; ``why_not_working`` is derived ONLY from recorded
outcome-evidence fields — it never invents an explanation.

Buckets (mutually exclusive, exactly one per store):
  - ``working_well``       campaign succeeding: outcome MEETS_TARGET / causal
                           CONFIRMED, or health >= on-track threshold
  - ``recovering``         positive momentum, not yet at target (MONITOR band
                           with recovery > 0, or REVIEW_ZONE)
  - ``needs_intervention`` approval-gated recommendations awaiting a human
                           decision (ESCALATE / PAUSE / EXTEND / diversified)
  - ``insufficient_data``  no forecast signal (NEEDS_REVIEW / no-data route);
                           flagged for analyst review, never scored
  - ``watch``              on the radar without momentum (MONITOR with
                           recovery <= 0) — no action required yet
"""
from __future__ import annotations

import html
from typing import Any, Mapping, Sequence

from guardrails import choice_pair, cost_of_inaction, risk_of

BUCKET_WORKING = "working_well"
BUCKET_RECOVERING = "recovering"
BUCKET_INTERVENE = "needs_intervention"
BUCKET_NO_DATA = "insufficient_data"
BUCKET_WATCH = "watch"

BUCKET_ORDER = (BUCKET_INTERVENE, BUCKET_WORKING, BUCKET_RECOVERING, BUCKET_WATCH, BUCKET_NO_DATA)

HEALTH_ON_TRACK = 70.0   # scorer's HEALTH_HIGH decision boundary


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _num(rec: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(rec.get(key, default))
    except (TypeError, ValueError):
        return default


def latest_by_store(records: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    """Latest recommendation record per store (append-only log: last wins)."""
    latest: dict[int, Mapping[str, Any]] = {}
    for record in records:
        store_id = record.get("store_id")
        if isinstance(store_id, int):
            latest[store_id] = record
    return latest


def _no_signal(rec: Mapping[str, Any]) -> bool:
    """The no-data route: nothing was scored, flagged for analyst review."""
    return (
        rec.get("recommendation") == "NEEDS_REVIEW"
        and _num(rec, "store_health_score") == 0.0
        and rec.get("forecast_signal_available") is False
    )


def classify_store(rec: Mapping[str, Any]) -> str:
    """Exactly one bucket per record; first match wins (deterministic)."""
    if _no_signal(rec):
        return BUCKET_NO_DATA
    if rec.get("requires_human_approval") is True:
        return BUCKET_INTERVENE
    outcome = _mapping(rec.get("outcome_evidence"))
    causal = _mapping(outcome.get("causal_evidence"))
    if outcome.get("target_assessment") == "MEETS_TARGET" or causal.get("assessment_state") == "CONFIRMED":
        return BUCKET_WORKING
    if _num(rec, "store_health_score") >= HEALTH_ON_TRACK:
        return BUCKET_WORKING
    if _num(rec, "recovery_pct") > 0 or outcome.get("target_assessment") == "REVIEW_ZONE":
        return BUCKET_RECOVERING
    return BUCKET_WATCH


def why_not_working(rec: Mapping[str, Any]) -> str:
    """Deterministic 'why not working' from recorded evidence ONLY.

    Priority: measured outcome evidence first (what actually happened), then
    the decision record's own reason. Never speculative."""
    outcome = _mapping(rec.get("outcome_evidence"))
    causal = _mapping(outcome.get("causal_evidence"))
    assessment = outcome.get("target_assessment")
    causal_state = causal.get("assessment_state")
    uplift = outcome.get("actual_uplift_pct")
    did = causal.get("did_uplift_pct")

    if assessment == "NEGATIVE":
        parts = [f"campaign measured negative lift ({uplift}% vs own baseline)"]
        if did is not None:
            parts.append(f"matched-control DiD is {did}% — controls outperformed, so the "
                         "observed move was market drift, not the campaign")
        return "; ".join(parts) + "."
    if causal_state == "REFUTED":
        return ("causal evidence REFUTED: matched controls outperformed the treated "
                "store, so the raw lift was market drift rather than campaign effect.")
    if assessment == "REVIEW_ZONE":
        return (f"lift ({uplift}%) sits in the review zone (within noise) — "
                "inconclusive, not yet decision-grade evidence.")
    if assessment in ("INSUFFICIENT", "NOT_DUE", "PARTIAL", "INVALID", "CONTRADICTORY"):
        return (f"outcome evidence is {assessment} — awaiting the 56-day baseline plus "
                "14-day evaluation window; coverage gaps are evidence, not backfilled.")
    return str(rec.get("reason") or "") or "no recorded reason; review the recommendation record."


def _entry(rec: Mapping[str, Any], decision_status: str | None,
           attention: Mapping[str, Any] | None = None) -> dict[str, Any]:
    outcome = _mapping(rec.get("outcome_evidence"))
    causal = _mapping(outcome.get("causal_evidence"))
    entry: dict[str, Any] = {
        "store_id": rec.get("store_id"),
        "recommendation": rec.get("recommendation"),
        "confidence": rec.get("confidence"),
        "store_health_score": rec.get("store_health_score"),
        "recovery_pct": rec.get("recovery_pct"),
        "days_remaining": rec.get("days_remaining"),
        "recommendation_id": rec.get("recommendation_id"),
        "decision_status": decision_status,
        "uplift_pct": outcome.get("actual_uplift_pct"),
        "did_uplift_pct": causal.get("did_uplift_pct"),
        "causal_state": causal.get("assessment_state"),
        "reason": str(rec.get("reason") or ""),
    }
    if attention is not None:
        entry["attention_rank"] = attention.get("attention_rank")
        entry["attention_tier"] = attention.get("attention_tier")
    if rec.get("requires_human_approval") is True:
        entry["why_not_working"] = why_not_working(rec)
        # Choice architecture (guardrails/__init__.py): for an approval-gated
        # store, surface the default-vs-fallback option set, the reversibility
        # of the action, and the cost of doing nothing - so the board tells a
        # stakeholder WHAT to decide, not just THAT something needs deciding.
        pair = choice_pair(rec.get("recommendation", ""))
        if pair is not None:
            entry["default_action"], entry["fallback_action"] = pair
        entry["risk"] = risk_of(rec.get("recommendation", ""))
        entry["cost_of_inaction"] = cost_of_inaction(rec)
    elif _no_signal(rec):
        entry["why_not_working"] = "no forecast signal for this store — cannot evaluate without data."
    return entry


def _decision_status(rec: Mapping[str, Any], store_id: int, pending_store_ids: set[int]) -> str | None:
    """Pending vs decided status for one store (awaiting / approved / rejected)."""
    if store_id in pending_store_ids:
        return "awaiting_decision"
    if "decided_at" in rec:
        return "approved" if rec.get("approved") is True else "rejected"
    return None


def _sort_buckets(buckets: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Deterministic ordering: intervention bucket by attention rank, others by store."""
    for name in buckets:
        if name == BUCKET_INTERVENE:
            buckets[name].sort(key=lambda e: (
                e.get("attention_rank") is None,          # unranked last
                e.get("attention_rank") or 0,             # monitor's urgency order
                e.get("store_id") or 0,
            ))
        else:
            buckets[name].sort(key=lambda e: (e.get("store_id") or 0))
    return buckets


def _build_questions(buckets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """The three stakeholder questions the board answers, derived from buckets."""
    return {
        "which_stores_are_recovering": [e["store_id"] for e in buckets[BUCKET_RECOVERING]],
        "which_stores_need_intervention": [e["store_id"] for e in buckets[BUCKET_INTERVENE]],
        "where_campaign_is_working": [e["store_id"] for e in buckets[BUCKET_WORKING]],
        "where_it_is_not_working_and_why": [
            {"store_id": e["store_id"], "why": e.get("why_not_working")}
            for e in buckets[BUCKET_INTERVENE] + buckets[BUCKET_WATCH]
            if e.get("why_not_working")
        ],
    }


def build_board(records: Sequence[Mapping[str, Any]],
                ranked_attention: Sequence[Mapping[str, Any]] | None = None,
                pending_store_ids: set[int] | None = None) -> dict[str, Any]:
    """Assemble the full board from persisted recommendation records.

    ``ranked_attention``: output of ``app.monitor.rank_attention`` for the
    pending-approval queue (used only for ordering the intervention bucket).
    ``pending_store_ids``: stores still awaiting a human decision; entries get
    ``decision_status="awaiting_decision"``, decided stores get the decision.
    """
    pending_store_ids = pending_store_ids or set()
    attention_by_store = {r.get("store_id"): r for r in (ranked_attention or [])
                          if isinstance(r, Mapping)}
    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in BUCKET_ORDER}

    for store_id, rec in sorted(latest_by_store(records).items()):
        bucket = classify_store(rec)
        status = _decision_status(rec, store_id, pending_store_ids)
        buckets[bucket].append(_entry(
            rec, status, attention_by_store.get(store_id),
        ))

    _sort_buckets(buckets)
    counts = {name: len(entries) for name, entries in buckets.items()}
    return {
        "total_stores": sum(counts.values()),
        "counts": counts,
        "buckets": buckets,
        "questions": _build_questions(buckets),
    }
def render_board_html(board: Mapping[str, Any],
                      title: str = "Retail Decision Intelligence — Store Board") -> str:
    """Server-rendered read-only HTML view (no client JS, no external assets).

    All dynamic strings are HTML-escaped; the board is evidence-cited text.
    """
    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else "-"))

    tiles = "".join(
        f'<div class="tile {esc(name)}"><span class="n">{esc(count)}</span>'
        f'<span class="label">{esc(name.replace("_", " "))}</span></div>'
        for name, count in board.get("counts", {}).items()
    )

    headings = {
        BUCKET_INTERVENE: "Needs intervention — awaiting a human decision",
        BUCKET_WORKING: "Campaign working well",
        BUCKET_RECOVERING: "Recovering (positive momentum, not yet at target)",
        BUCKET_WATCH: "Watch — no action required yet",
        BUCKET_NO_DATA: "Insufficient data — flagged for analyst review",
    }
    columns = ("<tr><th>Store</th><th>Recommendation</th><th>Health</th><th>Recovery %</th>"
               "<th>Days left</th><th>Uplift %</th><th>DiD %</th><th>Status</th>"
               "<th>Decision (default → fallback / risk)</th><th>Why / reason</th></tr>")
    sections: list[str] = []
    for name in BUCKET_ORDER:
        entries = board.get("buckets", {}).get(name, [])
        rows = []
        for e in entries:
            why = e.get("why_not_working") or e.get("reason") or "-"
            cost = e.get("cost_of_inaction")
            if cost:
                why = f"{why} — {cost}"
            # Decision options are only meaningful on the intervention bucket.
            if e.get("default_action") and e.get("fallback_action"):
                decision = f"{e['default_action']} → {e['fallback_action']} / {e.get('risk') or '-'}"
            else:
                decision = "-"
            rows.append(
                f"<tr><td>{esc(e.get('store_id'))}</td><td>{esc(e.get('recommendation'))}</td>"
                f"<td>{esc(e.get('store_health_score'))}</td><td>{esc(e.get('recovery_pct'))}</td>"
                f"<td>{esc(e.get('days_remaining'))}</td><td>{esc(e.get('uplift_pct'))}</td>"
                f"<td>{esc(e.get('did_uplift_pct'))}</td><td>{esc(e.get('decision_status') or '-')}</td>"
                f"<td class='decision'>{esc(decision)}</td><td class='why'>{esc(why)}</td></tr>"
            )
        body = "".join(rows) or "<tr><td colspan='10' class='empty'>none</td></tr>"
        sections.append(
            f"<h2>{esc(headings[name])} ({esc(len(entries))})</h2>"
            f"<table>{columns}{body}</table>"
        )

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{esc(title)}</title><style>"
        "body{font-family:system-ui,sans-serif;margin:24px;color:#111}"
        ".tiles{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}"
        ".tile{border:1px solid #ddd;border-radius:8px;padding:10px 16px;min-width:120px}"
        ".tile .n{font-size:28px;font-weight:700;display:block}"
        ".tile .label{color:#555;font-size:12px;text-transform:capitalize}"
        ".needs_intervention{border-color:#c0392b}.working_well{border-color:#1e8449}"
        "table{border-collapse:collapse;width:100%;margin-bottom:28px;font-size:13px}"
        "th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}"
        "th{background:#f5f5f5}.why{max-width:340px}.decision{max-width:200px}.empty{color:#999;text-align:center}"
        "</style></head><body>"
        f"<h1>{esc(title)}</h1><div class='tiles'>{tiles}</div>{''.join(sections)}"
        "</body></html>"
    )
