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
import json
import re
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


def _margin_cell(row: Mapping[str, Any]) -> str:
    """Watchlist margin cell: measured figure wins, else the prior projection.

    Realised outcome renders plain; the projection carries a "~" prefix and
    "(proj.)" suffix so a reviewer never reads a prior as cash. "—" when
    neither exists (no baseline on record yet).
    """
    realised = row.get("realised_margin")
    if isinstance(realised, (int, float)) and not isinstance(realised, bool):
        return _money_label(realised)
    projected = row.get("projected_margin")
    if isinstance(projected, (int, float)) and not isinstance(projected, bool):
        return f"~{_money_label(projected)} (proj.)"
    return "—"


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


def _days_left_label(value: Any) -> str:
    """Days remaining in the recovery window; em dash when absent."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return str(int(value))


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
.hero-q {{ font-size: 22px; font-weight: 700; margin: 18px 0 2px; }}
.trend {{ background: #fff; border: 1px solid #d9e5e2; padding: 12px 16px; margin: 14px 0; }}
.trend svg {{ width: 100%; height: auto; display: block; }}
.trend .legend {{ font-size: 12px; color: {GRAY}; }}
.swatch {{ display: inline-block; width: 10px; height: 10px; margin: 0 4px 0 12px; }}
.fresh {{ color: {GRAY}; font-size: 12px; margin: 4px 0 0; }}
.why-top {{ background: #fff; border: 1px solid #d9e5e2; padding: 18px 20px; margin: 16px 0; }}
.pill {{ display: inline-block; background: {TEAL}; color: #fff; padding: 5px 14px;
         border-radius: 3px; font-weight: 700; font-size: 15px; letter-spacing: .02em; }}
.why-kpis span {{ display: inline-block; margin: 14px 26px 0 0; font-size: 11px; color: {GRAY};
                  text-transform: uppercase; letter-spacing: .04em; }}
.why-kpis b {{ display: block; font-size: 21px; color: {INDIGO}; font-weight: 700;
               text-transform: none; letter-spacing: 0; margin-bottom: 2px; }}
.why-lead {{ font-size: 15px; line-height: 1.6; margin: 16px 0 2px; }}
.cite {{ display: inline-block; background: {MINT}; border: 1px solid {TEAL}; color: {INDIGO};
         padding: 0 6px; font-size: 11px; border-radius: 9px; white-space: nowrap;
         font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
.badges span {{ display: inline-block; border: 1px solid #d9e5e2; background: #fff;
                padding: 6px 12px; margin: 4px 8px 4px 0; font-size: 13px; }}
code {{ background: {MINT}; padding: 1px 5px; font-size: 12px;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
details.raw {{ margin: 22px 0; }}
details.raw summary {{ cursor: pointer; font-size: 13px; color: {GRAY}; }}
details.raw pre {{ background: #fff; border: 1px solid #d9e5e2; padding: 12px;
                   font-size: 11px; line-height: 1.5; overflow: auto; max-height: 340px; }}
"""

_NAV = [("/ui", "Dashboard"), ("/ui/approvals", "Approvals"),
        ("/ui/simulate", "Simulator"), ("/ui/evals", "Eval history")]


def render_page(title: str, active: str, body: str) -> str:
    links = "".join(
        f'<a href="{href}" class="{"active" if href == active else ""}">{esc(label)}</a>'
        for href, label in _NAV
    )
    # The footer credits the author (see LICENSE) and names dunnhumby as the data
    # provider only, so the page reads as a personal project built on their public
    # dataset rather than as a dunnhumby product. Two spans, so the flexbox
    # `space-between` in _CSS puts the attribution left and the credit right.
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{esc(title)} · retail decision agent</title>
<style>{_CSS}</style></head><body>
<div class="rule"></div><div class="wrap">
<h1>{esc(title)}</h1>
<nav>{links}</nav>
{body}
<footer><span>© 2026 Ashish Kumar</span><span>data: dunnhumby</span></footer>
</div></body></html>"""


def _kv_table(rows: Sequence[tuple[str, Any]]) -> str:
    cells = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in rows)
    return f'<table>{cells}</table>' if cells else '<p class="muted">No data.</p>'


def _trend_svg(labels: Sequence[str], logged: Sequence[int], decided: Sequence[int]) -> str:
    """Grouped daily bars (logged teal, decided indigo) as dependency-free SVG.

    Inputs are plain ints from the handler's log/ledger bucketing, so the
    chart can only ever show persisted history — no invented series.
    """
    peak = max(list(logged) + list(decided) + [0])
    if peak <= 0:
        return ""
    width, height, base, top = 560, 120, 104, 8
    n = len(labels)
    slot = width / max(n, 1)
    bar_w = max(2.0, slot / 4)
    bars: list[str] = []
    for i, (label, lo, de) in enumerate(zip(labels, logged, decided)):
        x = i * slot + slot / 2
        for value, color, shift in ((lo, TEAL, -bar_w), (de, INDIGO, 0.0)):
            h = (float(value) / peak) * (base - top) if value else 0.0
            if h > 0:
                bars.append(
                    f"<rect x='{x + shift:.1f}' y='{base - h:.1f}' width='{bar_w:.1f}' "
                    f"height='{h:.1f}' fill='{color}'><title>{esc(label)}: {int(value)}</title></rect>"
                )
        if i % 2 == 0 or n <= 7:
            bars.append(
                f"<text x='{x:.1f}' y='118' font-size='8' text-anchor='middle' "
                f"fill='{GRAY}'>{esc(label)}</text>"
            )
    return (
        f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='daily activity'>"
        f"{''.join(bars)}</svg>"
    )


def _dashboard_hero(pending_count: int, stores: int, measured: int) -> str:
    return (
        f"<p class='hero-q'>Are we turning stores around?</p>"
        f"<p class='muted'>{pending_count} of {stores} stores awaiting a human decision"
        f"{f' · {measured} measured outcomes' if measured else ''}.</p>"
    )


def _dashboard_kpis(pending_count: int, realised: str, realised_sub: str,
                    projected: str, projected_sub: str, regret: str,
                    oldest: str, swept_label: str) -> str:
    return (
        f'<div class="kpis">'
        f'<span><b>{pending_count}</b> pending approvals</span>'
        f'<span><b>{esc(realised)}</b> realised margin<br><small>{esc(realised_sub)}</small></span>'
        f'<span><b>{esc(projected)}</b> projected margin<br><small>{esc(projected_sub)}</small></span>'
        f'<span><b>{esc(regret)}</b> regret vs do-nothing</span>'
        f'<span><b>{esc(oldest)}</b> oldest wait</span>'
        f'<span><b>{esc(swept_label)}</b> last sweep</span></div>'
    )


def _dashboard_activity(trend: Mapping[str, Any]) -> str:
    labels = list(trend.get("labels", []))
    logged = [int(v) for v in trend.get("logged", [])]
    decided = [int(v) for v in trend.get("decided", [])]
    svg = _trend_svg(labels, logged, decided) if labels else ""
    if not svg:
        return ("<h2>Activity</h2>"
                "<p class='muted'>No recommendations or decisions recorded yet.</p>")
    return (
        f"<h2>Activity — last {len(labels)} days</h2>"
        f"<div class='trend'>{svg}"
        f"<p class='legend'><span class='swatch' style='background:{TEAL}'></span>"
        f"recommendations logged ({sum(logged)})"
        f"<span class='swatch' style='background:{INDIGO}'></span>"
        f"human decisions ({sum(decided)})</p></div>"
    )


def _dashboard_queue(attention: Sequence[Mapping]) -> str:
    if not attention:
        return '<p class="muted">Attention queue is empty.</p>'
    queue_rows = "".join(
        f"<tr><td>{esc(r.get('store_id'))}</td>"
        f"<td>{esc(r.get('recommendation'))}</td>"
        f"<td>{esc(r.get('store_health_score'))}</td>"
        f"<td>{esc(_recovery_label(r.get('recovery_pct')))}</td>"
        f"<td>{esc(_days_left_label(r.get('days_remaining')))}</td>"
        f"<td>{esc(_priority_label(r))}</td>"
        f"<td>{esc(_margin_cell(r))}</td>"
        f"<td>{esc(r.get('confidence'))}</td>"
        f"<td>{esc(working_label(r.get('campaign_working')))}</td>"
        f"<td><a href='/ui/why/{esc(r.get('store_id'))}'>why?</a></td></tr>"
        for r in attention[:20]
    )
    return (f'<h2>Store recovery watchlist ({len(attention)})</h2>'
            f'<p class="muted">The stores that explain whether the strategy is working. '
            f'<a href="/ui/approvals">Decide on all pending</a>.</p>'
            f'<table><tr><th>Store</th><th>Recommendation</th><th>Health</th>'
            f'<th>Recovery %</th><th>Days left</th><th>Priority</th><th>Margin</th>'
            f'<th>Confidence</th><th>Campaign working?</th><th></th></tr>{queue_rows}</table>')


def _dashboard_log(log_entries: Sequence[Mapping]) -> str:
    if not log_entries:
        return '<p class="muted">Decision log is empty.</p>'
    log_rows = "".join(
        f"<tr><td>{esc(e.get('store_id'))}</td><td>{esc(e.get('recommendation'))}</td>"
        f"<td>{esc(e.get('forecast_status'))}</td><td>{esc(e.get('decided_at') or e.get('run_timestamp'))}</td></tr>"
        for e in list(log_entries)[-10:][::-1]
    )
    return (f'<h2>Recent decision log</h2>'
            f'<table><tr><th>Store</th><th>Recommendation</th><th>Forecast</th><th>At</th></tr>'
            f'{log_rows}</table>')


def _dashboard_fresh(swept_label: str) -> str:
    return (f"<p class='fresh'>Data refreshed {esc(swept_label)}"
            f"{' (live sweep)' if swept_label != 'never' else ' (run a sweep to refresh)'}.</p>"
            f"<p class='fresh'>Free-tier upstreams sleep when idle: this page serves cached decisions "
            f"instantly and wakes the forecast + campaign APIs behind it — "
            f"refresh again in ~1 min if numbers look stale.</p>")


def _dashboard_money(quality: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    measured = quality.get("measured_intervention_count") or 0
    if measured:
        realised = _money_label(quality.get("total_incremental_margin"))
        realised_sub = f"measured across {measured} store{'s' if measured != 1 else ''}"
        regret = _money_label(quality.get("total_regret_vs_do_nothing"), signed=False)
    else:
        realised = "—"
        realised_sub = "no measured outcomes yet"
        regret = "—"
    projection = quality.get("projection") if isinstance(quality.get("projection"), dict) else {}
    projected_total = projection.get("projected_total_margin")
    projected_n = projection.get("projected_store_count") or 0
    if isinstance(projected_total, (int, float)) and projected_n:
        projected = _money_label(projected_total)
        projected_sub = (f"DiD +2.84% prior over {projected_n} store{'s' if projected_n != 1 else ''} "
                         f"— projection, NOT measured")
    else:
        projected, projected_sub = "—", "no projectable baseline"
    return realised, realised_sub, regret, projected, projected_sub


def _dashboard_waits(pending: Sequence[Mapping], sweep_status: Mapping[str, Any]) -> tuple[str, str]:
    waits = [r.get("generated_at") for r in pending if isinstance(r, Mapping)]
    oldest = "—"
    if waits:
        try:
            oldest = _age_label(sorted(str(w) for w in waits if isinstance(w, str))[0])
        except Exception:
            oldest = "—"
    swept = _age_label(sweep_status.get("last_sweep_at"))
    swept_label = f"{swept} ago" if swept != "—" else "never"
    return oldest, swept_label


def render_dashboard(pending: Sequence[Mapping], log_entries: Sequence[Mapping],
                     attention: Sequence[Mapping],
                     quality: Mapping[str, Any] | None = None,
                     sweep_status: Mapping[str, Any] | None = None,
                     decisions: Sequence[Mapping] | None = None,
                     trend: Mapping[str, Any] | None = None,
                     total_stores: int | None = None) -> str:
    quality = quality if isinstance(quality, Mapping) else {}
    sweep_status = sweep_status if isinstance(sweep_status, Mapping) else {}
    trend = trend if isinstance(trend, Mapping) else {}
    measured = quality.get("measured_intervention_count") or 0
    realised, realised_sub, regret, projected, projected_sub = _dashboard_money(quality)
    oldest, swept_label = _dashboard_waits(pending, sweep_status)
    stores = total_stores if isinstance(total_stores, int) else len(
        {r.get("store_id") for r in log_entries if isinstance(r, Mapping)})
    hero = _dashboard_hero(len(pending), stores, measured)
    kpis = _dashboard_kpis(len(pending), realised, realised_sub, projected,
                           projected_sub, regret, oldest, swept_label)
    activity = _dashboard_activity(trend)
    queue = _dashboard_queue(attention)
    log = _dashboard_log(log_entries)
    fresh = _dashboard_fresh(swept_label)
    return f'{hero}{kpis}{activity}{queue}{log}{fresh}'


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


# --- Why page (Priority 4 detail view) ---------------------------------------

_WHY_MARKER_RE = re.compile(r"\[(rec|event|src):([^\]\s]+)\]")
_CITE_KIND = {
    "rec": ("Recommendation record", "the persisted decision this narrative explains"),
    "event": ("Lifecycle / outcome event", "an intervention or measured-outcome record"),
    "src": ("Methodology source", "a cited chunk of the in-repo methodology corpus"),
}


def _display(value: Any) -> str:
    """Readable cell value: em dash for missing, Yes/No for flags, str otherwise."""
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _short_ref(ref: Any) -> str:
    """Compact id for an inline chip; the full id stays in the Citations table."""
    tail = str(ref).rsplit("-", 1)[-1]
    return tail[:8] + ("…" if len(tail) > 8 else "")


def _why_narrative(text: str) -> str:
    """Narrative prose with ``[rec:/event:/src:]`` markers as inline chips.

    Sentences are escaped and rendered verbatim - only the citation markers are
    restyled - so the page still reads exactly what the API served (the
    frontend contract's "never rewrite the sentences" rule).
    """
    text = text or ""
    out: list[str] = []
    last = 0
    for match in _WHY_MARKER_RE.finditer(text):
        kind, ref = match.group(1), match.group(2)
        label = _CITE_KIND.get(kind, (kind,))[0]
        out.append(esc(text[last:match.start()]))
        out.append(f"<span class='cite' title='{esc(label)}: {esc(ref)}'>"
                   f"{esc(kind)} {esc(_short_ref(ref))}</span>")
        last = match.end()
    out.append(esc(text[last:]))
    return "".join(out)


def _why_citations(citations: Any) -> str:
    rows = []
    for cite in citations or []:
        if not isinstance(cite, Mapping):
            continue
        kind = str(cite.get("type") or "")
        label, note = _CITE_KIND.get(kind, (kind or "—", ""))
        rows.append(f"<tr><td>{esc(label)}</td><td><code>{esc(cite.get('id'))}</code></td>"
                    f"<td>{esc(cite.get('detail') or note)}</td></tr>")
    if not rows:
        return '<p class="muted">No citations - nothing to ground.</p>'
    return ("<table><tr><th>Type</th><th>Reference</th><th>What it is</th></tr>"
            f"{''.join(rows)}</table>")


def _why_methodology(methodology: Any) -> str:
    rows = []
    for chunk in methodology or []:
        if not isinstance(chunk, Mapping):
            continue
        score = chunk.get("score")
        score_text = (f"{float(score):.2f}"
                      if isinstance(score, (int, float)) and not isinstance(score, bool) else "—")
        rows.append(f"<tr><td>{esc(chunk.get('title') or '—')}</td>"
                    f"<td>{esc(chunk.get('part'))}</td><td>{esc(score_text)}</td>"
                    f"<td class='muted'>{esc(chunk.get('license_note') or '—')}</td></tr>")
    if not rows:
        return '<p class="muted">No methodology retrieved for this question.</p>'
    return ("<table><tr><th>Methodology note</th><th>Part</th><th>Relevance</th>"
            f"<th>Licence</th></tr>{''.join(rows)}</table>")


def _why_guard(guard: Any) -> str:
    """Guard results as badges: a pass is green, a degrade is grey, a veto red."""
    labels = {"numeric_grounding": "Numbers traceable to the evidence",
              "citations": "Citations resolve", "llm": "LLM layer"}
    badges = []
    for key, value in (guard or {}).items():
        text = str(value)
        passed = text == "passed"
        color = TEAL if passed else (GRAY if ("unavailable" in text or "off" in text) else "#b3261e")
        badges.append(f"<span><b style='color:{color}'>{'&#10003;' if passed else '&#8226;'}</b> "
                      f"{esc(labels.get(key, key))}: {esc(text)}</span>")
    return f"<div class='badges'>{''.join(badges)}</div>" if badges else '<p class="muted">No guard results.</p>'


def _why_evidence_rows(latest: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """The engine's own inputs, in the order a reviewer checks them."""
    recovery = _recovery_label(latest.get("recovery_pct"))
    direction = latest.get("recovery_direction")
    if recovery != "—" and direction:
        recovery = f"{recovery} ({direction})"
    forecast = str(latest.get("forecast_status") or "—")
    if latest.get("forecast_signal_available") is False:
        forecast += " - no forecast signal (business data gap, not an integration error)"
    rows = [
        ("Recommendation", _display(latest.get("recommendation"))),
        ("Confidence", _display(latest.get("confidence"))),
        ("Store health score", _display(latest.get("store_health_score"))),
        ("Recovery", recovery),
        ("Window", f"{_display(latest.get('days_elapsed'))} days elapsed · "
                   f"{_days_left_label(latest.get('days_remaining'))} remaining"),
        ("Forecast", forecast),
        ("Baseline → current forecast",
         f"{_display(latest.get('baseline_forecast'))} → {_display(latest.get('current_forecast'))}"),
        ("Human approval required", _display(latest.get("requires_human_approval"))),
        ("Reason (engine wording)", latest.get("reason")),
        ("Recommendation ID", latest.get("recommendation_id")),
        ("Generated at", _display(latest.get("generated_at"))),
    ]
    return [(label, value) for label, value in rows if value not in (None, "")]


def render_why(store_id: int, result: Mapping) -> str:
    """Readable answer for one store: what the engine decided, and why.

    Renders only what the API served. The bulky ``evidence`` payload that the
    JSON contract exposes for machine consumers is collapsed under "Raw
    evidence" rather than dumped as ``key: value`` lines, so the page reads as
    an explanation instead of a Python repr.
    """
    evidence = result.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    latest = evidence.get("latest_recommendation")
    latest = latest if isinstance(latest, Mapping) else None
    narrative = next((result[k] for k in ("narrative", "answer", "explanation")
                      if isinstance(result.get(k), str) and result[k]), "")

    parts: list[str] = []
    question = result.get("question")
    if question:
        parts.append(f"<p class='muted'>Question asked: {esc(question)}</p>")

    if latest is None:
        parts.append(f"<div class='banner info'>"
                     f"{esc(narrative or f'No recommendation on record for store {store_id}.')}</div>")
        parts.append("<p class='muted'>Once a recommendation exists for this store, "
                     "this page cites the record it was computed from.</p>")
    else:
        parts.append(
            "<div class='why-top'>"
            f"<span class='pill'>{esc(latest.get('recommendation') or 'UNKNOWN')}</span>"
            "<div class='why-kpis'>"
            f"<span><b>{esc(_display(latest.get('confidence')))}</b>confidence</span>"
            f"<span><b>{esc(_recovery_label(latest.get('recovery_pct')))}</b>recovery</span>"
            f"<span><b>{esc(_days_left_label(latest.get('days_remaining')))}</b>days remaining</span>"
            f"<span><b>{esc(_display(latest.get('forecast_status')))}</b>forecast</span>"
            "</div>"
            f"<p class='why-lead'>{_why_narrative(narrative)}</p>"
            "</div>"
            "<h2>Evidence the engine used</h2>"
            f"{_kv_table(_why_evidence_rows(latest))}"
        )

    parts.append("<h2>Citations</h2>")
    parts.append(_why_citations(result.get("citations")))
    parts.append("<h2>Methodology applied</h2>")
    parts.append(_why_methodology(result.get("methodology")))
    parts.append("<h2>Grounding checks</h2>")
    parts.append(_why_guard(result.get("guard")))
    if evidence:
        parts.append("<details class='raw'><summary>Raw evidence (JSON)</summary>"
                     f"<pre>{esc(json.dumps(evidence, indent=2, sort_keys=True, default=str))}</pre>"
                     "</details>")
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
