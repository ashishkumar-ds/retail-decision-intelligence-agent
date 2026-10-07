"""Server-rendered operator site for the decision agent (The Complete Journey theme).

Plain HTML + forms: zero new dependencies, zero client-side framework. Every
page renders the same data the JSON API serves - the UI is a view, never a
second source of truth. Mutating forms post the approval token directly and
are validated by the same fail-closed token gate as the JSON endpoints.

Palette (matches docs/diagrams/architecture-dunnhumby.*):
    teal #38b2ab, mint #ebf7f5, indigo #26245c, gray #cccccc, black text.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

TEAL = "#38b2ab"
MINT = "#ebf7f5"
INDIGO = "#26245c"
GRAY = "#666666"


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def working_label(value: Any) -> str:
    """Human-readable campaign verdict for table cells.

    The queue carries the full ``is_campaign_working`` mapping; rendering the
    raw dict leaks Python reprs into the operator UI. Collapse it to the
    verdict plus its evidence instead.
    """
    if isinstance(value, dict):
        working = value.get("working")
        evidence = value.get("evidence") or "no evidence"
        if working is True:
            return f"Yes — {evidence}"
        if working is False:
            return f"No — {evidence}"
        return f"Not yet — {evidence}"
    if value is None:
        return "—"
    return str(value)


def _recovery_label(value: Any) -> str:
    """Signed recovery percent for table cells (records persist ``recovery_pct``).

    Missing or non-numeric values render as an em dash rather than a blank
    or a Python repr; bools are excluded (``True`` is not ``100%``).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{float(value):+.1f}%"


def _money_label(value: Any, signed: bool = True) -> str:
    """Money figure for tiles and cells (store currency, hence no symbol).

    Missing/non-numeric renders as an em dash; bools are excluded.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{float(value):+,.2f}" if signed else f"{float(value):,.2f}"


def _age_label(iso_value: Any) -> str:
    """Short relative age ("3d", "5h", "20m") for ISO timestamps; "—" when absent."""
    if not isinstance(iso_value, str):
        return "—"
    try:
        moment = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "—"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - moment).total_seconds()
    if seconds < 0:
        return "0m"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _priority_label(row: Mapping[str, Any]) -> str:
    """Rank + reason the queue is ordered the way it is (both already computed).

    The reason strings already carry the tier wording ("Must-act: …",
    "Should-review: …"), so the tier word is only prepended when the reason
    does not start with it — no stutter.
    """
    tier = {0: "Must-act", 1: "Should-review", 2: "Watch"}.get(row.get("attention_tier"))
    rank = row.get("attention_rank")
    reason = row.get("attention_reason") or ""
    if tier and reason.startswith(tier):
        head = reason
    elif tier and reason:
        head = f"{tier} — {reason}"
    else:
        head = reason or tier or "—"
    if isinstance(rank, int) and head != "—":
        return f"#{rank} · {head}"
    return head


_CSS = f"""
body {{ font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; margin: 0;
       color: {INDIGO}; background: {MINT}; }}
.rule {{ height: 4px; background: {TEAL}; }}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 0 24px; }}
h1 {{ font-size: 26px; margin: 24px 0 6px; }}
nav a {{ margin-right: 18px; color: {INDIGO}; text-decoration: none; font-weight: 600; }}
nav a.active {{ color: {TEAL}; border-bottom: 3px solid {TEAL}; padding-bottom: 4px; }}
table {{ border-collapse: collapse; width: 100%; background: #fff; margin: 14px 0; }}
th, td {{ border: 1px solid #d9e5e2; padding: 8px 10px; font-size: 14px; text-align: left; }}
th {{ background: {MINT}; }}
.chip {{ display: inline-block; background: {TEAL}; color: #fff; padding: 3px 10px;
        font-size: 12px; border-radius: 3px; }}
.banner {{ padding: 10px 14px; margin: 14px 0; font-size: 14px; }}
.banner.ok {{ background: #e6f4ef; border-left: 5px solid {TEAL}; }}
.banner.err {{ background: #fdecea; border-left: 5px solid #b3261e; }}
.banner.info {{ background: #eef1fb; border-left: 5px solid {INDIGO}; }}
form.inline {{ display: inline; }}
input, button {{ font-size: 14px; padding: 6px 8px; border: 1px solid {INDIGO};
                 border-radius: 3px; background: #fff; }}
button {{ background: {TEAL}; color: #fff; border: none; cursor: pointer;
          padding: 7px 16px; font-weight: 600; }}
footer {{ margin-top: 40px; border-top: 1px solid #ccc; padding: 10px 0 30px;
          color: {GRAY}; font-size: 12px; display: flex; justify-content: space-between; }}
.muted {{ color: {GRAY}; font-size: 12px; }}
.kpis span {{ display: inline-block; background: #fff; border: 1px solid #d9e5e2;
              padding: 12px 22px; margin: 6px 14px 6px 0; }}
.kpis b {{ font-size: 24px; display: block; }}
"""

_NAV = [("/ui", "Dashboard"), ("/ui/approvals", "Approvals"),
        ("/ui/simulate", "Simulator"), ("/ui/evals", "Eval history")]


def render_page(title: str, active: str, body: str) -> str:
    links = "".join(
        f'<a href="{href}" class="{"active" if href == active else ""}">{esc(label)}</a>'
        for href, label in _NAV
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{esc(title)} · retail decision agent</title>
<style>{_CSS}</style></head><body>
<div class="rule"></div><div class="wrap">
<h1>{esc(title)}</h1>
<nav>{links}</nav>
{body}
<footer><span>© 2026 retail decision intelligence agent · all rights reserved</span>
<span><b>dunnhumby</b></span></footer>
</div></body></html>"""


def _kv_table(rows: Sequence[tuple[str, Any]]) -> str:
    cells = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in rows)
    return f'<table>{cells}</table>' if cells else '<p class="muted">No data.</p>'


def render_dashboard(pending: Sequence[Mapping], log_entries: Sequence[Mapping],
                     attention: Sequence[Mapping],
                     quality: Mapping[str, Any] | None = None,
                     sweep_status: Mapping[str, Any] | None = None) -> str:
    quality = quality if isinstance(quality, Mapping) else {}
    sweep_status = sweep_status if isinstance(sweep_status, Mapping) else {}
    measured = quality.get("measured_intervention_count") or 0
    realised = _money_label(quality.get("total_incremental_margin")) if measured else "—"
    regret = _money_label(quality.get("total_regret_vs_do_nothing"), signed=False) if measured else "—"
    waits = [r.get("generated_at") for r in pending if isinstance(r, Mapping)]
    oldest = "—"
    if waits:
        try:
            oldest = _age_label(sorted(str(w) for w in waits if isinstance(w, str))[0])
        except Exception:
            oldest = "—"
    swept = _age_label(sweep_status.get("last_sweep_at"))
    swept = f"{swept} ago" if swept != "—" else "never"
    kpis = (
        f'<div class="kpis">'
        f'<span><b>{len(pending)}</b> pending approvals</span>'
        f'<span><b>{esc(realised)}</b> realised margin</span>'
        f'<span><b>{esc(regret)}</b> regret vs do-nothing</span>'
        f'<span><b>{esc(oldest)}</b> oldest wait</span>'
        f'<span><b>{esc(swept)}</b> last sweep</span></div>'
    )
    queue_rows = "".join(
        f"<tr><td>{esc(r.get('store_id'))}</td>"
        f"<td>{esc(r.get('recommendation'))}</td>"
        f"<td>{esc(r.get('store_health_score'))}</td>"
        f"<td>{esc(_recovery_label(r.get('recovery_pct')))}</td>"
        f"<td>{esc(r.get('days_remaining') if isinstance(r.get('days_remaining'), (int, float)) and not isinstance(r.get('days_remaining'), bool) else '—')}</td>"
        f"<td>{esc(_priority_label(r))}</td>"
        f"<td>{esc(_money_label(r.get('realised_margin')))}</td>"
        f"<td>{esc(r.get('confidence'))}</td>"
        f"<td>{esc(working_label(r.get('campaign_working')))}</td>"
        f"<td><a href='/ui/why/{esc(r.get('store_id'))}'>why?</a></td></tr>"
        for r in attention[:20]
    )
    queue = (f'<h2>Attention queue ({len(attention)})</h2>'
             f'<table><tr><th>Store</th><th>Recommendation</th><th>Health</th>'
             f'<th>Recovery %</th><th>Days left</th><th>Priority</th><th>Margin</th>'
             f'<th>Confidence</th><th>Campaign working?</th><th></th></tr>{queue_rows}</table>'
             if attention else '<p class="muted">Attention queue is empty.</p>')
    log_rows = "".join(
        f"<tr><td>{esc(e.get('store_id'))}</td><td>{esc(e.get('recommendation'))}</td>"
        f"<td>{esc(e.get('forecast_status'))}</td><td>{esc(e.get('decided_at') or e.get('run_timestamp'))}</td></tr>"
        for e in list(log_entries)[-10:][::-1]
    )
    log = (f'<h2>Recent decision log</h2>'
           f'<table><tr><th>Store</th><th>Recommendation</th><th>Forecast</th><th>At</th></tr>'
           f'{log_rows}</table>' if log_entries else '<p class="muted">Decision log is empty.</p>')
    return f'{kpis}{queue}{log}'


def render_approvals(pending: Sequence[Mapping], banner: str | None = None) -> str:
    banner_html = f'<div class="banner {"ok" if "approved" in banner or "already" in banner else "err"}">{esc(banner)}</div>' if banner else ""
    rows = "".join(
        f"<tr><td>{esc(r.get('store_id'))}</td>"
        f"<td>{esc(r.get('recommendation'))}</td>"
        f"<td>{esc(r.get('store_health_score'))}</td>"
        f"<td>{esc(r.get('confidence'))}</td>"
        f"<td><a href='/ui/why/{esc(r.get('store_id'))}'>why?</a></td>"
        f"<td><form class='inline' method='post' action='/ui/approve/{esc(r.get('store_id'))}'>"
        f"<input type='password' name='token' placeholder='token' required>"
        f"<input type='hidden' name='actor' value='operator-site'>"
        f"<button>Approve</button></form> "
        f"<form class='inline' method='post' action='/ui/reject/{esc(r.get('store_id'))}'>"
        f"<input type='password' name='token' placeholder='token' required>"
        f"<input type='hidden' name='actor' value='operator-site'>"
        f"<button>Reject</button></form></td></tr>"
        for r in pending[:50]
    )
    table = (f'<table><tr><th>Store</th><th>Recommendation</th><th>Health</th>'
             f'<th>Confidence</th><th></th><th>Decision (bearer token)</th></tr>{rows}</table>'
             if pending else '<p class="muted">No pending approvals. Run /recommendations/run first.</p>')
    return f'''{banner_html}<p class="muted">Decisions are double-gated at write time
(guardrails + verifier re-run). A missing server-side token disables these
actions entirely (503) - the fail-closed contract holds in the UI too.</p>{table}'''


def render_simulate(store_id: str = "", started_day: str = "",
                    result: Mapping | None = None, error: str | None = None) -> str:
    form = f'''<form method='get' action='/ui/simulate'>
<input type='number' name='store_id' placeholder='store id' value='{esc(store_id)}' required>
<input type='number' name='started_day' placeholder='started day (e.g. 650)' value='{esc(started_day)}' required>
<button>Simulate</button></form>'''
    if error:
        body = f"{form}<div class='banner err'>{esc(error)}</div>"
    elif result is None:
        body = f"{form}<p class='muted'>Replays the calibrated causal prior on the store's observed baseline. Read-only.</p>"
    else:
        sim = result.get("simulation") or {}
        base = sim.get("baseline") or {}
        proj = sim.get("projected") or {}
        did = proj.get("did_pct") or {}
        gr = proj.get("guardrail") or {}
        inc = proj.get("incremental_value") or {}
        mom = sim.get("own_momentum_pct")
        body = form + (
            f"<div class='banner info'>{esc(result.get('verdict', ''))}</div>"
            + _kv_table([
                ("evidence_state", result.get("evidence_state")),
                ("baseline mean daily sales", base.get("mean_daily_sales")),
                ("coverage days", base.get("coverage_days")),
                ("own momentum %", (mom or {}).get("value")),
                ("projected DiD % (point)", did.get("point")),
                ("projected DiD % CI-95", (did.get("ci95") or [None, None])),
                ("guardrail", gr.get("assessment_state")),
                ("scale-up eligible", gr.get("scale_up_eligible")),
                ("incremental value (point)", inc.get("point")),
                ("incremental value CI-95", (inc.get("ci95") or [None, None])),
            ])
            + "".join(f"<p class='muted'>{esc(lim)}</p>" for lim in result.get("limitations", []))
        )
    return body


def render_why(store_id: int, result: Mapping) -> str:
    parts = [f"<h2>Why store {esc(store_id)}?</h2>"]
    for key in ("narrative", "answer", "explanation"):
        if isinstance(result.get(key), str):
            parts.append(f"<p>{esc(result[key])}</p>")
            break
    for key in ("citations", "records_cited", "grounding"):
        value = result.get(key)
        if value:
            parts.append(f"<h3>{esc(key)}</h3>")
            parts.append(f"<pre style='white-space:pre-wrap;font-size:13px'>{esc(value)}</pre>")
    for key, value in result.items():
        if key in ("narrative", "answer", "explanation", "citations", "records_cited", "grounding"):
            continue
        parts.append(f"<p class='muted'>{esc(key)}: {esc(value)}</p>")
    return "".join(parts)


def render_evals(records: Sequence[Mapping]) -> str:
    rows = "".join(
        f"<tr><td>{esc(r.get('run_at'))}</td><td>{esc(r.get('kind', 'golden'))}</td>"
        f"<td>{esc(r.get('total'))}</td><td>{esc(r.get('passed'))}</td>"
        f"<td>{esc(r.get('failed'))}</td><td>{esc(', '.join(r.get('failed_case_ids', [])) or '-')}</td></tr>"
        for r in list(records)[-30:][::-1]
    )
    table = (f"<table><tr><th>Run at</th><th>Kind</th><th>Total</th><th>Passed</th>"
             f"<th>Failed</th><th>Failed cases</th></tr>{rows}</table>" if rows
             else "<p class='muted'>No eval runs recorded yet. Run python evaluation/run_evals.py.</p>")
    return table
