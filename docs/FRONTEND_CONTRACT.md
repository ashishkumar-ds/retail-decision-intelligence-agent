# Frontend contract — read-only stakeholder board (v0 / Vercel)

The spec to paste into **v0.dev** (or hand to any frontend tool) to build an
external dashboard for this API. One rule governs everything below: **the UI
is a view, never a second source of truth** (`presentation/site.py`) — it
renders what the API says and computes nothing.

## Hard boundaries (non-negotiable)

1. **GET endpoints only.** No approve/reject/execute UI, no bearer token in
   the browser, no forms that mutate. Decisions live in the server-rendered
   `/ui` behind the Human Gate.
2. **Never recompute.** Render `store_health_score`, `recovery_pct`,
   `confidence`, margins exactly as served. No client-side scoring, no
   derived "improvement" numbers.
3. **Measured vs projected stays visible.** Money from an outcome renders
   plain; a prior projection renders with a `~` prefix and `(proj.)` label.
   Never sum the two.
4. **`forecast_status` is a first-class badge:** `AVAILABLE` (fresh),
   `NO_DATA` (business gap), `ERROR` (upstream down — show "feed stale",
   never pretend it is zero).

## Setup

- Backend: Render service, e.g. `https://retail-decision-intelligence-agent.onrender.com`
- Render → Environment → add `CORS_ALLOW_ORIGINS=https://your-board.vercel.app` → save
  (unset = the API refuses cross-origin reads by default; same-origin `/ui` unaffected)
- Vercel → Environment variable `NEXT_PUBLIC_API_BASE` = the Render URL
- Poll every **60 s**; on `502`/`504` (Render free-tier cold start) show
  "service waking up…" and retry **once after 30 s** before showing an error.

## Endpoints (all JSON, all unauthenticated)

### `GET /health`
`{"status": "ok", "scheduler": {...}, "execution_connector": "dry-run", "features": {...}}` — footer "connection OK" probe.

### `GET /board` — the main page
```jsonc
{
  "total_stores": 5,
  "counts": { "needs_intervention": 2, "working_well": 3, "recovering": 0,
              "watch": 0, "insufficient_data": 0 },
  "buckets": { "<bucket>": [ /* entries below */ ] },
  "questions": { "which_stores_are_recovering": [], "which_stores_need_intervention": [299, 317],
                 "where_campaign_is_working": [289, 31582, 31642],
                 "where_it_is_not_working_and_why": [ {"store_id": 299, "why": "..."} ] }
}
```
Bucket entry (always): `store_id, recommendation, confidence, store_health_score,
recovery_pct, days_remaining, recommendation_id, decision_status, forecast_status,
uplift_pct, did_uplift_pct, causal_state, reason`.
Additions on approval-gated rows: `attention_rank, attention_tier, why_not_working,
default_action, fallback_action, risk, cost_of_inaction` — render these as the
decision card ("decide at /ui/approvals", NOT as buttons here).

### `GET /recommendations`
`{"total_stores": 5, "read_only": true, "recommendations": [ /* records */ ]}`
— record carries `recommendation, confidence, reason, store_health_score,
recovery_pct, recovery_direction, recovery_velocity, days_remaining,
requires_human_approval, forecast_status, generated_at, outcome_evidence?,
retail_context?, trajectory?`.

### `GET /why/{store_id}?question=...` — the detail page
`{"store_id": 317, "narrative": "… [rec:…] [event:…]", "citations": [{"type","id","detail"}],
  "methodology": [{"chunk_id","title","part","score"}], "evidence": {...},
  "guard": {"numeric_grounding": "passed", "citations": "passed"}}`
— render `narrative` as trusted rich text only where `[rec:/event:/src:]`
markers appear; never rewrite the sentences.

### `GET /pending-approvals`
`{"count": 2, "pending": [ /* records awaiting a human */ ]}` — read-only count.

## Design tokens (match the server-rendered UI)

`teal #38b2ab` primary · `mint #ebf7f5` background · `indigo #26245c` text ·
border `#d9e5e2`. Footer: `© 2026 retail decision intelligence agent · dunnhumby`.

---

## Paste-ready v0 prompt

```text
Build a read-only dashboard called "Retail Decision Board" (Next.js + Tailwind + shadcn/ui),
client-side fetching from process.env.NEXT_PUBLIC_API_BASE. Two pages:

1) Board: KPI tiles (total_stores; counts from /board), then one card per bucket
   in this order needs_intervention, working_well, recovering, watch, insufficient_data,
   each listing store rows: store_id, recommendation, health, recovery_pct,
   days_remaining, forecast_status badge, uplift_pct/did_uplift_pct/causal_state.
   Rows with default_action show a decision card: default vs fallback, risk label,
   cost_of_inaction text - static text only, NO action buttons.
2) Store detail /store/[id]: fetch /why/{id} and render narrative + citation chips.

Rules: GET only, no auth, no mutations; poll /board every 60s; on 502/504 show
"service waking up..." and retry once after 30s; render numbers exactly as served
(no client math); money with "~" prefix + "(proj.)" when flagged as projection;
forecast_status ERROR renders a "feed stale" chip. Palette: teal #38b2ab, mint #ebf7f5,
indigo #26245c; footer "© 2026 retail decision intelligence agent · dunnhumby".
```
