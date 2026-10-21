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
  - ``working_well``       campaign causally succeeding: outcome MEETS_TARGET
                           AND causal CONFIRMED, or health >= on-track
                           threshold. Raw MEETS_TARGET alone is working but
                           not proven causal — see _outcome_working.
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

from guardrails import choice_pair, cost_of_inaction, requires_human_approval, risk_of

BUCKET_WORKING = "working_well"
BUCKET_RECOVERING = "recovering"
BUCKET_INTERVENE = "needs_intervention"
BUCKET_NO_DATA = "insufficient_data"
BUCKET_WATCH = "watch"

BUCKET_ORDER = (BUCKET_INTERVENE, BUCKET_WORKING, BUCKET_RECOVERING, BUCKET_WATCH, BUCKET_NO_DATA)

# Display labels: the no-data bucket reads as review workload, never dead data.
# Every NEEDS_REVIEW row stays approval-gated (guardrails.APPROVAL_REQUIRED_
# RECOMMENDATIONS) and ranked in the attention queue — the label only fixes the
# stakeholder story, not the gate.
BUCKET_LABELS = {
    BUCKET_INTERVENE: "Needs intervention",
    BUCKET_WORKING: "On track",
    BUCKET_RECOVERING: "Building momentum",
    BUCKET_WATCH: "Watch",
    BUCKET_NO_DATA: "Needs review (no data)",
}

HEALTH_ON_TRACK = 70.0   # scorer's HEALTH_HIGH decision boundary

# Stakeholder wording for the engine's action vocabulary and reversibility
# tiers. The codes stay on the entry (machine consumers, /why citations); the
# labels are what a board prints, so both board surfaces read the same sentence
# and a non-technical reviewer never sees EXTEND_INTERVENTION as a headline.
ACTION_LABELS = {
    "CONTINUE": "Continue as planned",
    "MONITOR": "Monitor only",
    "EXTEND_INTERVENTION": "Extend the intervention",
    "PAUSE_INTERVENTION": "Pause the intervention",
    "ESCALATE": "Escalate to a senior reviewer",
    "NEEDS_REVIEW": "Review the data gap",
    "RETARGET_SEGMENT": "Retarget the segment",
    "TIMING_SHIFT": "Shift the campaign timing",
    "REALLOCATE_BUDGET": "Reallocate budget",
}
# Tier semantics are guardrails' (commitment, not process weight) — see
# guardrails/__init__.py. "cautious" alone reads as vague to a stakeholder.
COMMITMENT_LABELS = {
    "reversible": "Undoable this cycle",
    "cautious": "Commits this cycle's budget",
    "irreversible": "Moves committed money",
}
# The tier label describes budget exposure. A review has none, so the generic
# reversible label ("Undoable this cycle") would still imply a budget action.
ACTION_COMMITMENT_LABELS = {
    "NEEDS_REVIEW": "No spend — review only",
}


def action_label(code: Any) -> str:
    """Stakeholder wording for an action code; degrades to the code itself."""
    text = str(code or "").strip()
    if not text:
        return "-"
    return ACTION_LABELS.get(text, text.replace("_", " ").capitalize())


def commitment_label(tier: Any, action: Any = None) -> str:
    code = str(action or "")
    if code in ACTION_COMMITMENT_LABELS:
        return ACTION_COMMITMENT_LABELS[code]
    return COMMITMENT_LABELS.get(str(tier or ""), str(tier or "-"))


# decision_status is a machine token ("awaiting_decision"); a board prints it
# as a sentence, and an already-decided store must not still read "Decision
# needed" - the engine re-decides every sweep, so a row can be both.
DECISION_STATUS_LABELS = {
    "awaiting_decision": "Awaiting a decision",
    "approved": "Approved",
    "rejected": "Rejected",
}


def decision_status_label(status: Any) -> str | None:
    if status in (None, ""):
        return None
    return DECISION_STATUS_LABELS.get(str(status), str(status).replace("_", " "))


def format_as_of(value: Any) -> str:
    """Readable timestamp for the HTML view; the raw value if unparseable.

    Display only - the clock is never converted. A zero offset prints as
    "UTC" instead of the noisier "+00:00"; a non-zero offset is preserved.
    """
    text = str(value or "")
    if not text:
        return ""
    try:
        from datetime import datetime
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return text
    offset = stamp.strftime("%z")
    if offset == "+0000":
        zone = " UTC"
    elif len(offset) == 5:
        zone = f" UTC{offset[:3]}:{offset[3:]}"
    else:
        zone = ""
    return stamp.strftime("%d %b %Y, %H:%M") + zone


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
    if rec.get("recommendation") != "NEEDS_REVIEW":
        return False
    if _num(rec, "store_health_score") != 0.0:
        return False
    # note: pending-store / legacy records may omit the flag or carry only
    # forecast_status=NO_DATA|ERROR — treat anything but an explicit True as
    # no-signal so NO_DATA rows can't fall through to needs_intervention.
    if rec.get("forecast_signal_available") is True:
        return False
    return rec.get("forecast_status", "NO_DATA") in ("NO_DATA", "ERROR")


def _gated(rec: Mapping[str, Any]) -> bool:
    """Whether the human gate holds this record.

    guardrails' policy is the single source of truth; the persisted flag counts
    as an additional yes, never as the only one. Log rows written before the
    flag existed still describe a gated action — a NEEDS_REVIEW row without the
    flag is still a review a human owns — and keying on the flag alone let the
    board read "1 needs a decision" while 47 gated stores sat next to it.
    """
    if rec.get("requires_human_approval") is True:
        return True
    return requires_human_approval(str(rec.get("recommendation") or ""))


def _outcome_working(outcome: Mapping[str, Any]) -> str | None:
    """Causal working verdict from outcome evidence, else None.

    Mirrors app/monitor.is_campaign_working: MEETS_TARGET + CONFIRMED DiD is
    working (causal); MEETS_TARGET without CONFIRMED is working but not
    causal — a positive operational signal, never the causal claim. Callers
    decide which verdict their surface needs; the board only buckets
    ``working_well`` on the causal one.
    """
    if outcome.get("target_assessment") != "MEETS_TARGET":
        return None
    causal = _mapping(outcome.get("causal_evidence"))
    if causal.get("assessment_state") == "CONFIRMED":
        return "causal"
    return "raw"


def classify_store(rec: Mapping[str, Any]) -> str:
    """Exactly one bucket per record; first match wins (deterministic)."""
    if _no_signal(rec):
        return BUCKET_NO_DATA
    if rec.get("requires_human_approval") is True:
        return BUCKET_INTERVENE
    outcome = _mapping(rec.get("outcome_evidence"))
    if _outcome_working(outcome) == "causal":
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


def _entry(rec: Mapping[str, Any], decision_status: str | None, bucket: str,
           attention: Mapping[str, Any] | None = None) -> dict[str, Any]:
    outcome = _mapping(rec.get("outcome_evidence"))
    causal = _mapping(outcome.get("causal_evidence"))
    entry: dict[str, Any] = {
        "store_id": rec.get("store_id"),
        # The row's own state word, from the bucket the engine put it in. A
        # surface that groups buckets (the stakeholder tiles) still prints the
        # true state per row, so "no action needed" can never overstate.
        "state": BUCKET_LABELS[bucket],
        "bucket": bucket,
        "recommendation": rec.get("recommendation"),
        "confidence": rec.get("confidence"),
        "store_health_score": rec.get("store_health_score"),
        "recovery_pct": rec.get("recovery_pct"),
        "days_remaining": rec.get("days_remaining"),
        "recommendation_id": rec.get("recommendation_id"),
        "generated_at": rec.get("generated_at"),
        "decision_status": decision_status,
        "decision_status_label": decision_status_label(decision_status),
        # Forecast SLO signal: AVAILABLE (fresh) vs NO_DATA (business gap) vs
        # ERROR (integration failure — feed down, last-good NOT cached, the
        # store sits in NEEDS_REVIEW until the feed recovers).
        "forecast_status": rec.get("forecast_status"),
        "uplift_pct": outcome.get("actual_uplift_pct"),
        "did_uplift_pct": causal.get("did_uplift_pct"),
        "causal_state": causal.get("assessment_state"),
        "evidence_state": outcome.get("evidence_state"),
        # Directional (forecast-derived, explicitly NOT measured uplift):
        # baseline/current predicted sales, day-in-window, signed momentum.
        # Absent on pre-change log records — renders as "—" until next sweep.
        "baseline_forecast": rec.get("baseline_forecast"),
        "current_forecast": rec.get("current_forecast"),
        "days_elapsed": rec.get("days_elapsed"),
        "recovery_direction": rec.get("recovery_direction"),
        "recovery_velocity": rec.get("recovery_velocity"),
        "directional_note": (
            "Early forecast signal — measured uplift follows once the evaluation window closes."
            if rec.get("forecast_signal_available") is True
            or rec.get("forecast_status") == "AVAILABLE" else None
        ),
        "reason": str(rec.get("reason") or ""),
    }
    if attention is not None:
        entry["attention_rank"] = attention.get("attention_rank")
        entry["attention_tier"] = attention.get("attention_tier")
    if _gated(rec):
        entry["requires_human_approval"] = True
        if _no_signal(rec):
            # A gated store with no forecast signal is a decision the human
            # owns: the engine cannot evaluate it, so the choice is review the
            # gap or leave it alone. Stated once, in the same shape as every
            # other gated row.
            entry["why_not_working"] = (
                "No forecast signal for this store — the campaign effect cannot be "
                "evaluated until the feed recovers."
            )
        else:
            entry["why_not_working"] = why_not_working(rec)
        # Choice architecture (guardrails/__init__.py): for a gated store,
        # surface the default-vs-fallback option set, the reversibility of the
        # action, and the cost of doing nothing - so the board tells a
        # stakeholder WHAT to decide, not just THAT something needs deciding.
        pair = choice_pair(rec.get("recommendation", ""))
        if pair is not None:
            entry["default_action"], entry["fallback_action"] = pair
            entry["default_action_label"] = action_label(pair[0])
            entry["fallback_action_label"] = action_label(pair[1])
        entry["risk"] = risk_of(rec.get("recommendation", ""))
        entry["commitment_label"] = commitment_label(
            entry["risk"], rec.get("recommendation")
        )
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
            rec, status, bucket, attention_by_store.get(store_id),
        ))

    _sort_buckets(buckets)
    counts = {name: len(entries) for name, entries in buckets.items()}
    latest = latest_by_store(records)
    # note: the gated workload spans buckets (NEEDS_REVIEW is gated but
    # classified as no-data) — one hero count so the gate never reads 0. The
    # count follows the guardrails policy, not the persisted flag, so a row
    # written before the flag existed still counts as a decision a human owns.
    needs_decision = sum(1 for entries in buckets.values() for e in entries
                         if e.get("requires_human_approval") is True)
    # Freshness the stakeholder can check: the newest sweep among the records
    # this board is actually showing. The poll interval is a property of the
    # screen; this is a property of the data.
    as_of = max((str(r.get("generated_at")) for r in latest.values()
                 if r.get("generated_at")), default=None)
    return {
        "total_stores": sum(counts.values()),
        "needs_decision": needs_decision,
        "as_of": as_of,
        # Display-ready, so no surface re-formats a served stamp into the
        # viewer's timezone: the board prints the record's own clock and offset.
        "as_of_display": format_as_of(as_of) or None,
        "counts": counts,
        "buckets": buckets,
        "questions": _build_questions(buckets),
    }
# --- the server-rendered HTML view -------------------------------------------
# Tabular copy lives in constants, not in the render function: the gate in CI
# fails any function that reaches D, and prose in a loop is complexity that buys
# nothing.

BOARD_HEADINGS = {
    BUCKET_INTERVENE: "Needs intervention — awaiting a human decision",
    BUCKET_WORKING: "Campaign working well — on track",
    BUCKET_RECOVERING: "Building momentum — positive, not yet at target",
    BUCKET_WATCH: "Watch — flat or declining, no action required yet",
    BUCKET_NO_DATA: "Needs review (no data) — no forecast signal, gated for a human",
}
# Stated once per bucket, never on every row: the 47 identical sentences a
# per-row note produced told the reader nothing the count did not.
BOARD_NOTES = {
    BUCKET_NO_DATA: (
        "No forecast signal reached these stores, so the campaign effect cannot be "
        "evaluated for them. No data is not zero — it is no verdict. Each one is "
        "gated for a human: review the data gap, or leave it on monitor."
    ),
    BUCKET_INTERVENE: (
        "The engine has already chosen the options; a human picks one. Nothing on "
        "this page approves anything."
    ),
}
BOARD_COLUMNS = ("<tr><th>Store</th><th>Recommendation</th><th>Health</th><th>Recovery %</th>"
                 "<th>Days left</th><th>Status</th>"
                 "<th>Decision (recommended → alternative · commitment)</th>"
                 "<th>Why / reason</th></tr>")
BOARD_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:24px;color:#111}"
    ".ctx{color:#555;font-size:13px;margin:0 0 4px}"
    ".gate{font-size:15px;margin:0 0 16px}"
    ".tiles{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0 28px}"
    ".tile{border:1px solid #ddd;border-radius:8px;padding:10px 16px;min-width:120px}"
    ".tile .n{font-size:28px;font-weight:700;display:block}"
    ".tile .label{color:#555;font-size:12px;text-transform:capitalize}"
    ".needs_intervention{border-color:#c0392b}.working_well{border-color:#1e8449}"
    ".note{color:#555;font-size:12.5px;margin:0 0 10px;max-width:78ch}"
    "table{border-collapse:collapse;width:100%;margin-bottom:28px;font-size:13px}"
    "th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}"
    "th{background:#f5f5f5}.why{max-width:300px}.decision{max-width:250px}"
    ".cost{color:#555;font-size:12px}.empty{color:#999;text-align:center}"
)


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else "-"))


def _decision_cell(entry: Mapping[str, Any]) -> str:
    """Recommended → alternative, what it commits, and the counterfactual."""
    if not entry.get("default_action"):
        return "-"
    recommended = entry.get("default_action_label") or action_label(entry["default_action"])
    alternative = entry.get("fallback_action_label") or action_label(entry.get("fallback_action"))
    decision = f"{recommended} → {alternative} · {entry.get('commitment_label') or '-'}"
    cost = entry.get("cost_of_inaction")
    return f"{decision}<br><span class='cost'>{_esc(cost)}</span>" if cost else decision


def _row_html(entry: Mapping[str, Any]) -> str:
    why = entry.get("why_not_working") or entry.get("reason") or "-"
    days = entry.get("days_remaining")
    days_cell = f"{_esc(days)} days left" if days is not None else "-"
    return (
        f"<tr><td>{_esc(entry.get('store_id'))}</td>"
        f"<td>{_esc(action_label(entry.get('recommendation')))}</td>"
        f"<td>{_esc(entry.get('store_health_score'))}</td>"
        f"<td>{_esc(entry.get('recovery_pct'))}</td><td>{days_cell}</td>"
        f"<td>{_esc(entry.get('decision_status_label') or '-')}</td>"
        f"<td class='decision'>{_decision_cell(entry)}</td>"
        f"<td class='why'>{_esc(why)}</td></tr>"
    )


def _section_html(name: str, entries: Sequence[Mapping[str, Any]]) -> str:
    note = BOARD_NOTES.get(name)
    note_html = f"<p class='note'>{_esc(note)}</p>" if note else ""
    rows = "".join(_row_html(entry) for entry in entries)
    body = rows or "<tr><td colspan='8' class='empty'>none</td></tr>"
    return (f"<h2>{_esc(BOARD_HEADINGS[name])} ({_esc(len(entries))})</h2>"
            f"{note_html}<table>{BOARD_COLUMNS}{body}</table>")


def _root_causes_html(board: Mapping[str, Any]) -> str:
    """Optional analytics section (analytics/root_cause.py).

    Present only when the caller supplied it (the endpoint gates it behind
    ROOT_CAUSE_TAGGING_ENABLED, because rendering must not silently send text
    to a third party). Absent -> nothing rendered.
    """
    root_causes = board.get("root_causes") or {}
    if not root_causes.get("counts"):
        return ""
    rows = "".join(
        f"<tr><td>{_esc(label)}</td><td>{_esc(count)}</td></tr>"
        for label, count in sorted(root_causes["counts"].items(), key=lambda item: -item[1])
    )
    drivers = ", ".join(f"{_esc(label)} ({_esc(count)})"
                        for label, count in sorted(root_causes.get("driver_counts", {}).items(),
                                                   key=lambda item: -item[1]))
    return (
        f"<h2>Root causes across the estate ({_esc(root_causes.get('tagged'))} tagged)</h2>"
        f"<table><tr><th>Root cause</th><th>Stores</th></tr>{rows}</table>"
        f"<p class='note'>Drivers: {drivers or '-'}. "
        f"{_esc(root_causes.get('status'))} — labels are advisory analytics, "
        "never a decision input.</p>"
    )


def _board_context(board: Mapping[str, Any]) -> str:
    """Campaign identity and data freshness, both served by the API.

    Never inferred, and stated once for the page rather than per section.
    """
    campaign = board.get("campaign") if isinstance(board.get("campaign"), Mapping) else {}
    label = " ".join(str(campaign.get(key) or "") for key in ("label", "timing_window")).strip()
    return " · ".join(part for part in (
        label,
        f"{board.get('total_stores')} stores",
        f"data as of {board.get('as_of_display') or format_as_of(board.get('as_of'))}"
        if board.get("as_of") else "",
    ) if part)


def render_board_html(board: Mapping[str, Any],
                      title: str = "Retail Decision Intelligence — Store Board") -> str:
    """Server-rendered read-only HTML view (no client JS, no external assets).

    Trimmed to the same shape as the stakeholder board: one place states how
    fresh the data is, one place states the gated workload, and each row
    carries the three numbers a decision needs plus its one reason. The
    measured-uplift columns are gone — they stay empty until an evaluation
    window closes, and the reason text already names the measured lift when
    there is one. All dynamic strings are HTML-escaped; the board is
    evidence-cited text.
    """
    counts = board.get("counts") or {}
    tiles = "".join(
        f'<div class="tile {_esc(name)}"><span class="n">{_esc(count)}</span>'
        f'<span class="label">{_esc(BUCKET_LABELS.get(name, name))}</span></div>'
        for name, count in counts.items()
    )
    # note: zero-count buckets carry no decision value
    buckets = board.get("buckets", {})
    sections = [_section_html(name, buckets.get(name, []))
                for name in BUCKET_ORDER if buckets.get(name)]
    sections.append(_root_causes_html(board))
    # The gate counts every human-owned row, including the no-data reviews.
    # The tiles carry the decision/data-review split; the sentence says only
    # what the number means.
    gate_line = (f"<strong>{_esc(board.get('needs_decision'))}</strong> of "
                 f"{_esc(board.get('total_stores'))} stores are waiting on a human review.")

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{_esc(title)}</title><style>{BOARD_STYLE}</style></head><body>"
        f"<h1>{_esc(title)}</h1><p class='ctx'>{_esc(_board_context(board))}</p>"
        f"<p class='gate'>{gate_line}</p>"
        f"<div class='tiles'>{tiles}</div>{''.join(sections)}"
        "</body></html>"
    )
