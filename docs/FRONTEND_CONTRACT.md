# Frontend contract — stakeholder board (Next.js / Vercel)

The contract the stakeholder board (`app/`, deployed on Vercel) implements,
and the spec for any other read-only client of this API. One rule governs
everything below: **the UI is a view, never a second source of truth** — it
renders what the API says and computes nothing.

## Hard boundaries (non-negotiable)

1. **Reads are GET; there is exactly one client write.** The board may `POST
   /approve/{store_id}` through its own same-origin proxy (`/api/approve/{id}`),
   forwarding an `Authorization: Bearer <approval token>` the operator enters in
   the page. The token is never bundled, never stored server-side, and never
   prefilled; it may live in `sessionStorage` for the tab. The backend re-runs
   the guardrails + verifier at decision time and refuses with `401`/`403`/`409`/
   `503`. **Approve is per store, one call at a time — no bulk approve.** Reject,
   re-run and every other mutation stay in `/ui` on the API host.
   The API itself may **self-refresh its persisted evidence** on the first stale
   `GET /board` of the day (see Freshness). That is a server-side re-evaluation
   of the same cohort, accepts no client input, and is never a second client
   write path.
2. **Never recompute.** Render `store_health_score`, `recovery_pct`,
   `confidence`, margins exactly as served. No client-side scoring, no
   derived "improvement" numbers.
3. **Measured vs projected stays visible.** Money from an outcome renders
   plain; a prior projection renders with a `~` prefix and `(proj.)` label.
   Never sum the two.
4. **`forecast_status` is a first-class signal, not decoration:** `ERROR`
   (upstream down) renders "feed delayed" and `NO_DATA` (business gap) renders
   when the row is not already inside the no-forecast-signal group. `AVAILABLE`
   renders no badge — it is the default on every other row, and 37 identical
   "forecast available" chips are noise. Never render a missing forecast as zero.

## Setup

- Backend: Render service, e.g. `https://retail-decision-intelligence-agent.onrender.com`
- Render → Environment → add `CORS_ALLOW_ORIGINS=https://your-board.vercel.app` → save
  (unset = the API refuses cross-origin reads by default; same-origin `/ui` unaffected)
- Vercel → Environment variable `API_BASE` = the Render URL. Server-side only:
  the proxy (`app/api/[...path]/route.ts`) reads it directly and the browser
  only sees the same-origin `/api` proxy, never the upstream hostname
- Poll every **60 s**; on `502`/`504` (Render free-tier cold start) show
  "service waking up…" and retry **once after 30 s** before showing an error.

## Endpoints (all JSON, all unauthenticated)

### `GET /health`
`{"status": "ok", "scheduler": {...}, "execution_connector": "dry-run", "features": {...}}` —
footer connection probe: after the board's first successful load the footer
calls it **once** (no polling loop) and quietly states `API live`, or `API
waking` on a cold start/failure. Fail-silent — the footer never shows an error.

### `GET /board` — the main page
```jsonc
{
  "total_stores": 5,
  "needs_decision": 2,          // every gated row, counted across buckets
  "as_of": "2026-10-18T14:02:00+00:00",
  "as_of_display": "18 Oct 2026, 14:02 UTC",  // render this, never re-format
  "counts": { "needs_intervention": 2, "working_well": 3, "recovering": 0,
              "watch": 0, "insufficient_data": 0 },
  "buckets": { "<bucket>": [ /* entries below */ ] },
  "questions": { "which_stores_are_recovering": [], "which_stores_need_intervention": [299, 317],
                 "where_campaign_is_working": [289, 31582, 31642],
                 "where_it_is_not_working_and_why": [ {"store_id": 299, "why": "..."} ] }
}
```
Bucket entry (always): `store_id, state, bucket, recommendation, confidence,
store_health_score, recovery_pct, days_remaining, recommendation_id, generated_at,
decision_status, decision_status_label, forecast_status, uplift_pct,
did_uplift_pct, causal_state, reason`. `state` is the row's own bucket word - print
it even when a surface groups buckets, or the group heading overstates the rows in it.

Gated rows (`requires_human_approval`) additionally carry `why_not_working,
attention_rank, attention_tier, default_action, fallback_action, default_action_label,
fallback_action_label, risk, commitment_label, cost_of_inaction`. **Gating is a
property of the policy, not of the persisted flag** — a `NEEDS_REVIEW` row with no
forecast signal is gated (and counted in `needs_decision`) whether or not the flag
was written, so `needs_decision` is the real human workload. Render it; never
derive a gated count in the browser.

`NEEDS_REVIEW` is a review, not a budget approval: its action is "Review the data
gap" (fallback "Monitor only"), `risk` is `reversible`, and `commitment_label` is
"No spend — review only". It still counts in `needs_decision` because a human owns
the review. The three tiles carry the decision/data-review split; the gate
sentence says only "N of M stores are waiting on a human review" and must not
repeat the tile counts.

Print the `*_label` fields. `EXTEND_INTERVENTION`, `cautious` and
`awaiting_decision` are machine tokens; a stakeholder surface says "Extend the
intervention", "Commits this cycle's budget", "Awaiting a decision" — and a row
whose `decision_status` is already `approved`/`rejected` says so instead of
asking for the same decision again. Approve actions live on gated rows only
(`requires_human_approval === true` and not already decided) and call
`POST /api/approve/{id}`; see "Approval" below. For `insufficient_data` rows label
the same write "Mark reviewed", not "Approve".

### What belongs on a row, and what does not

A row carries the three numbers a decision needs (`store_health_score`,
`recovery_pct`, `days_remaining`) and one reason sentence. `confidence`,
`recovery_velocity`, `recovery_direction`, `baseline_forecast`/`current_forecast`,
`days_elapsed`, `uplift_pct`, `did_uplift_pct`, `causal_state` and `evidence_state`
are evidence for `/store/[id]`. Measured uplift is empty on every row until an
evaluation window closes: it is a caveat, stated once, not eight columns of `—`.

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
— strip the internal `[rec:/event:/src:]` citation markers before rendering; never
rewrite the sentences. **Never print a citation id, the raw `rec/event/src`
token, a methodology chunk or a source list** on this page: `citations` and
`methodology` are provenance for the operator `/ui/why` and the raw payload,
not stakeholder evidence.

### `GET /pending-approvals`
`{"count": 2, "pending": [ /* records awaiting a human */ ]}` — read-only count.

### `POST /approve/{store_id}` — the one write
Proxied same-origin as `POST /api/approve/{store_id}` with the operator's
`Authorization: Bearer <token>` forwarded unchanged; the proxy injects no
credential and allows no other POST path.

- `200` `{"message": "... approved.", "recommendation": {...}}`
- `401` missing/malformed header · `403` unknown token or viewer role
- `409` `{"detail": {"message": "Decision-time gate failed; approval refused.", "checks": {...}}}`
  — the guardrails/verifier re-run refused; **nothing was recorded**
- `503` no approval token configured server-side (fail closed)
- `404` store is not in the pending queue; it may already be decided

The dialog must: collect the token (password field), keep it in `sessionStorage`
for the tab only, show the served error, and refresh `/board` after a `200` so
the gate count and the row's `decision_status` come from the API. Never
optimistically mark a row approved on a `409`.

## Design tokens (match the server-rendered UI)

`teal #38b2ab` (brand bar, markers, dots - non-text only) · `teal-text #17645f`
(links, labels, eyebrows) · `mint #edf8f5` background · `indigo #26245c` headings ·
`ink #202044` body · `muted #4f6069` secondary text · border `#d7e5e1` ·
panel `#f6faf9`. The small-text tokens are the ones that clear WCAG AA 4.5:1 on
mint, white and panel — `#38b2ab` is 2.4:1 and must never carry text. Freshness
(`Data as of {as_of_display}`) rides the header identity line, right-aligned on
desktop and on its own left line on phones, wrapped in `<time datetime="{as_of}">`;
it is stated once and never repeated in the footer. Footer:
`© 2026 <LICENSE holder> · data: dunnhumby`.

`as_of` is the newest recommendation's evaluation time, not the wall clock. When
it predates today (UTC), the API re-evaluates the persisted cohort on that read
before serving, so the header stamp rolls forward without the UI re-formatting
anything — the UI still prints `as_of_display` exactly as served.

