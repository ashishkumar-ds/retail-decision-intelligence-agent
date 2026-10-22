# Deployment

The image is standard and boring: `python:3.12-slim`, non-root user, deps
pinned, one uvicorn process bound to `0.0.0.0:$PORT`. `docker compose up -d
--build` is the same thing locally.

```bash
docker compose up -d --build          # local
curl http://localhost:8001/health
```

## Render (the stable stakeholder URL)

**Two paths exist, and only one of them takes effect.** [`render.yaml`](../render.yaml)
in this repo is a Render *blueprint*: a service created with **New → Blueprint**
is re-applied from that file on every deploy, so the file wins. A service created
**by hand in the dashboard** (the path `scripts/deploy_render_wizard.sh` walks)
never reads `render.yaml`, so the dashboard wins. Do not mix them — a
hand-created service that is later edited by hand drifts silently, because
nothing reconciles the two. Pick one path and stay on it; every value below is
also carried in `render.yaml`, so the two stay in step only if you keep them so.

The consequence is the thing to remember — **whatever you do not set keeps its
code default, silently.** That is not hypothetical: the production service ran
with `SWEEP_ENABLED` unset, so the autonomous sweep was off while `/health`
looked perfectly healthy. **Read `/health` after every deploy** — it reports the
sweep's enabled/running state, its interval, its last error and the next tick,
which is the field that turns a silent default into a visible fact.

Prefer a guided walkthrough? The wizard opens each dashboard URL, generates the
approval token, records every value in `.env.local`, waits for the first deploy,
runs the checks below and records the URL:

```bash
bash scripts/deploy_render_wizard.sh
```

Manual click-through:

1. Dashboard → **New → Web Service** (inside a Project if you use Projects) →
   *Build and deploy from a Git repository* → this repo.
2. Language **Docker**, Branch `main`, Dockerfile path `./Dockerfile`.
3. **Health Check Path** `/health` — Render restarts a container that fails it.
   A cold start can take ~60s, so give the first deploy time.
4. Instance type **Starter** (always-on). See the storage trade-off below.
5. **Advanced → Add Disk**: name `agent-logs`, mount path `/srv/app/logs`,
   1 GB. Without it the audit trail is erased on every deploy.
6. **Environment**: set the variables in the table below. `APPROVAL_AUTH_TOKEN`
   is the only one the write paths need; while it is unset the service serves
   read-only (approve/reject/execute return 503), so a first deploy is safe.
7. Deploy. First boot runs the schema migrations (`storage/database.py`) and
   rebuilds the RAG corpus from `rag/sources/`. Auto-deploy on push is on by
   default — keep it, and let CI gate the commit.

### Environment: what actually changes behaviour

The complete list, with comments, is [`.env.example`](../.env.example); a test
fails if the code reads a variable that is not documented there.

| Variable | Production value | What it does — and what breaks without it |
| --- | --- | --- |
| `APPROVAL_AUTH_TOKEN` | **required** | Fail-closed gate on every write path. Unset → read-only service (503). Demo-grade bearer token: no rotation/expiry/OIDC — never expose write paths publicly without an additional control layer. |
| `SWEEP_ENABLED` | `0` in the published demo; `1` once the audit source is owned | Opt-in autonomous sweep. Unset → the daily sweep never runs (the default). It is `0` in the demo on purpose: the store universe comes from Project 2's audit feed, which this repo does not own, so a reset feed makes every tick fail (`/health` shows `sweeps_failed`). The board still self-heals from its own persisted cohort (see `BOARD_SELF_REFRESH`). Turn the sweep on when `CAMPAIGN_AUDIT_API_URL` points at a source you own. |
| `SWEEP_INTERVAL_SECONDS` | `86400` | Sweep cadence (daily). |
| `BOARD_SELF_REFRESH` | `1` (default) | On the first `GET /board` where any undecided store's latest record predates today (UTC) **or every undecided record is a technical `ERROR` with no usable forecast**, re-evaluate the persisted cohort against the live forecast and serve the new stamp. The outage trigger is bounded by the refresh cooldown (10 min) and a total outage never overwrites the last good snapshot. The campaign start is reconstructed from the board's own append-only records, so an emptied audit feed no longer freezes it. Set `0` to serve the stored snapshot without re-evaluation. |
| `FORECAST_API_URL` | the forecast service URL | Where actuals/predictions come from; the code default is the deployed Project 1 API. | SLO: single upstream, no ensemble/cache — `ERROR` (feed down) vs `NO_DATA` (business gap) per record on `/board`; outage leaves new stores in `NEEDS_REVIEW`. |
| `CAMPAIGN_AUDIT_API_URL` | **required for the sweep** | Project 2's read-only audit log, and the source of the store universe. Unset with no `CAMPAIGN_AUDIT_LOG_PATH` file → `POST /recommendations/run` answers 400 "No store_ids found in the audit log" and the daily sweep does nothing. |
| `RETAIL_STORE_CONTEXT_URL` | optional | P2 `datasets/stores.csv` for the store margin proxy; unset → code default. Unreachable → layer fails open, decisions unchanged. |
| `RETAIL_SKU_ROLLUP_PATH` | optional | Store×SKU rollup CSV for inventory-aware demand-vs-supply; unset → SKU layer off, decisions unchanged. |
| `DATABASE_URL` | Supabase Session pooler URL (see below); unset → SQLite on the disk. | `postgres://` for the pending-approval store. **Supabase shape:** `postgresql://postgres.REF:[PASSWORD]@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require` — Session pooler (port 5432, IPv4, supports prepared statements), **not** the direct host (IPv6-only, unreachable from Render). Makes pending-approvals durable + multi-instance safe; does **not** replace the JSONL audit logs (keep the disk). |
| `ROOT_CAUSE_TAGGING_ENABLED` | `false` | The board's root-cause section (third-party egress, redacted before sending). |
| `LLM_EXPLANATIONS_ENABLED`, `LLM_ADVISORY_ENABLED`, `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_MAX_TOKENS`, `LLM_API_KEY` (`ANTHROPIC_API_KEY` for the Anthropic provider) | off in git, on in the published demo | The off-path LLM: it re-words evidence the deterministic engine already decided on, and every failure degrades to the deterministic output. `LLM_API_KEY` is a secret — set it in the dashboard, never in the repo. See "Hosted LLM: the two ceilings" below. |
| `LLM_PER_IP_HOURLY_LIMIT`, `LLM_DAILY_CALL_BUDGET` | `20` / `250` | Spend guard for the two public endpoints that can call the model. `0` = unlimited. See below. |
| `APPROVAL_TOKENS` | optional | Named approvers with roles; supersedes the shared token and records `decided_by`. |
| `EXECUTION_CONNECTOR` | `dry-run` | Name surfaced in `/health`. Only shipped adapter is dry-run (journals intent, no external write). |

### Hosted LLM: the two ceilings

`/why` and `/advisory` are public, unauthenticated GETs. Switching the LLM on
there publishes a paid API call to anyone who finds the URL, so that layer is
capped twice — and the two caps fail in different directions on purpose:

| Ceiling | Env | When it is spent | Why it fails this way |
| --- | --- | --- | --- |
| Account-wide, per UTC day | `LLM_DAILY_CALL_BUDGET` (`250`) | The model is not called. The deterministic template is served with a `200`, and the guard says why: `guard.llm = "deterministic-template (daily LLM budget spent (250/250 calls today); template served)"` | A fact about the account, not about the visitor. The demo must keep working and keep costing nothing. |
| Per caller, rolling hour | `LLM_PER_IP_HOURLY_LIMIT` (`20`) | `429` with `Retry-After`. Nothing is consumed, so retry loops cannot push their own window forward | A fact about *this* caller. One loop is the caller's problem, not a reason to serve everybody templates. |

Callers are identified from the **last** `X-Forwarded-For` entry (the one Render
vouches for), so a forged header cannot buy extra quota.

The counters live in the process (`rag/llm_budget.py`), so they are
per-instance and reset on deploy. That is fine for a demo and it is stated here
rather than discovered later. The guard is on **spend, never on output**: the
decision path does not know it exists.

A spent budget is fail-closed but still answers `200`, so it must be visible:
`/metrics` reports `retail_llm_daily_calls_used` against
`retail_llm_daily_call_budget`, and every real call's `tokens_in` /
`tokens_out` / `cache_read` land in the off-path telemetry trail and surface as
`retail_offpath_llm_tokens_total` and
`retail_offpath_llm_cache_read_tokens_total`. The scheduled canary
(`.github/workflows/llm-smoke.yml`) makes one live call a night, so a retired
model or a rotated key is caught before a visitor is the one who notices.

Turning it on is two steps, and only the second one is secret:

1. `render.yaml` already declares the switches (it is committed; it holds no key).
2. Set `LLM_API_KEY` **in the Render dashboard** — it is `sync: false` in
   `render.yaml`, so the blueprint carries the name and the dashboard carries the
   value. Then verify on the live URL:

```bash
# a real rephrase, or an honest reason why not
curl -s https://retail-decision-intelligence-agent.onrender.com/why/317 | grep -o 'llm-grounded'
# watch the per-client ceiling engage
curl -sD- -o /dev/null https://retail-decision-intelligence-agent.onrender.com/why/317 | grep -i retry-after
```

### Durable storage: read this before calling it production

This system's **system of record is files**: the append-only recommendation
log, approval ledger, execution journal, phase-2 registry and the SQLite
pending-approval store all live under `logs/`. Container filesystems are
ephemeral, so **without a persistent disk every deploy or crash-restart
silently discards the audit trail** - and an audit trail you cannot reproduce
is the one thing this project promises.

Three honest options:

| Option | What you get | Cost |
| --- | --- | --- |
| Paid instance (Starter) + the disk added in the dashboard | The correct production posture: the ledger survives deploys. Trade-off: disks pin the service to one instance and disable zero-downtime deploys. | ~$7/mo instance + disk |
| Free tier, no disk added | A **demo URL**. Works completely, but state resets on every deploy/restart - say so out loud when you share it. | free |
| Migrate the JSONL stores to Postgres tables | Durability without a disk, multi-instance safe. Open work: `memory/history.py` is a pinned carve-out (append-only, fsynced, never rewritten), so it must move with its golden cases in the same commit. | engineering time |

`DATABASE_URL` alone does **not** solve this: it makes the pending-approval
store durable, not the JSONL logs.

### Supabase → Render (durable latest-record store on Postgres)

The app needs **zero code changes** for the durable store: `storage/database.py` +
`app/state.py` already select Postgres when `DATABASE_URL` is a `postgres://`
URL and run the versioned migrations on first connect. Reads are diskless-durable
too: `/board`, `/board/view`, `/recommendations` and the `/why` detail page fall
back to the durable latest-record store (one row per store, gated or not; the
approval queue is a filtered view) when the JSONL log is empty (see
`_readable_records` in `app/main.py`; log wins when present). One union feeds
every read surface, so a store the board lists is never unexplainable on its
detail page. This section is the click-through
(verified against Supabase docs 2026-10-10):

1. **Supabase Dashboard → your project → Connect → Postgres.** Copy the
   **Session pooler** string (host `aws-0-REGION.pooler.supabase.com`,
   port **5432**). Why session, not transaction/direct:
   - Render is IPv4-only → the direct host (IPv6-only, port 5432) is
     unreachable; the shared pooler is IPv4-only on every plan.
   - Session mode supports prepared statements; transaction mode does not —
     and this app opens one short connection per operation, which is exactly
     the session-mode shape (no server-side pooler tuning needed).
2. **Append `?sslmode=require`** (Supabase enforces SSL; psycopg defaults to
   `prefer`, which would silently fall back to plaintext). URL-encode the
   password (`#` → `%23`, `@` → `%40`, `/` → `%2F`). Final shape:
   `postgresql://postgres.REF:[PASSWORD]@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require`
3. **Verify locally before touching Render** (no secrets printed):
   ```bash
   DATABASE_URL='postgresql://...' python scripts/verify_supabase.py
   DATABASE_URL='postgresql://...' python -m pytest tests/test_live_postgres.py -q
   ```
   The probe checks URL shape → SSL flag → real connect → migrations
   (`pending_approvals` + `schema_migrations`). Tables are created by the
   app's own migrations — do **not** hand-create them in the SQL Editor.
4. **Render Dashboard → your service → Environment → `DATABASE_URL`** = the
   same string → Save (triggers redeploy). First boot re-runs migrations
   (idempotent) and the store flips to `postgres` (`PendingApprovalStore._dialect`).
   Keep the `agent-logs` disk mounted: the JSONL audit trail still lives there.
5. **Security notes (Supabase setup checklist):**
   - The app connects with the `postgres` service role over a direct Postgres
     wire — it does **not** go through the Data API, so no `GRANT anon/` RLS
     policy is needed or honoured for this path. Keep the tables out of the
     Data API: Dashboard → Database → API settings → leave
     `pending_approvals`/`schema_migrations` unexposed (or, if your project
     auto-grants `anon`/`authenticated` on `public`, run
     `REVOKE ALL ON pending_approvals, schema_migrations FROM anon, authenticated;`
     as defense in depth).
   - Never commit the URL: `.env.local` is gitignored/dockerignored; Render
     holds the secret (`render.yaml` declares `DATABASE_URL: sync: false` so
     the blueprint never carries a value).

### Post-deploy verification (one command)

```bash
bash scripts/verify_deployment.sh https://<service>.onrender.com [APPROVAL_TOKEN]
```

It checks liveness, that the deployed route count matches `main` (deploy drift),
that the read-only surface answers, that write paths fail closed, and — when you
pass a token — that the authenticated surfaces answer. Warnings are posture
notes (e.g. "the sweep is off"); failures mean the URL is not ready to share.

The closed loop, by hand:

```bash
BASE=https://<service>.onrender.com
TOKEN=<APPROVAL_AUTH_TOKEN>

curl -s $BASE/health | python -m json.tool           # liveness + sweep status
curl -s -H "Authorization: Bearer $TOKEN" $BASE/metrics | head
curl -s -H "Authorization: Bearer $TOKEN" $BASE/board > /dev/null && echo board ok

# the closed loop, in order
curl -s -X POST -H "Authorization: Bearer $TOKEN" $BASE/recommendations/run | head -c 300
curl -s -H "Authorization: Bearer $TOKEN" $BASE/pending-approvals     # expect a queue
curl -s -X POST -H "Authorization: Bearer $TOKEN" $BASE/approve/317
curl -s -X POST -H "Authorization: Bearer $TOKEN" $BASE/execute/317   # idempotent
curl -s -H "Authorization: Bearer $TOKEN" $BASE/executions            # journaled
```

Then, to prove the Postgres path against a real server:

```bash
DATABASE_URL=postgresql://... python -m pytest tests/test_live_postgres.py -q
```

### Operational notes

- **Secrets**: never baked into the image - `tests/test_image_contract.py`
  fails the build if the Dockerfile grows an `ENV *TOKEN*`-style line, if a
  runtime package is missing from the image, or if `.dockerignore` stops
  excluding `.env*` and `logs/`.
- **Alerts**: `ops/observability/alerts.yml` ships the rules worth having
  (decision concentration, sweep failures, sweep staleness, approval backlog,
  and three for the off-path LLM: guard-rejection surge, degradation to
  template, and a spent daily budget); `/metrics` is token-gated, so scrape with
  `authorization: Bearer`.
- **Egress**: `/metrics`, `/analytics/root-causes` and the board's root-cause
  section are the only surfaces that leave the process, and all reason text is
  numerically redacted before it is sent. `ROOT_CAUSE_TAGGING_ENABLED` gates
  the **board section only** (a page render must not silently call a third
  party); the `/analytics/root-causes` endpoint is always available — an
  authenticated call to it is the explicit consent, by design.
- **Rollback**: Render keeps previous deploys, but the *state* is the disk -
  rolling back code without the matching disk loses decisions recorded after
  it.

## Hosting alternatives considered (verified against platform docs, 2026-09-21)

| Platform | Verdict for this agent | Cost (always-on, durable) |
| --- | --- | --- |
| **Render (chosen)** | Docker web service + persistent disk + health checks, created by hand in the dashboard; the exact shape this app needs | **~$7.25/mo** (Starter $7 + 1 GB disk $0.25) |
| Railway | Same shape; Hobby $5/mo includes $5 usage; volumes supported; no spin-down on paid | ~$5–8/mo |
| Fly.io | Cheapest raw compute (shared-cpu-1x ≈ $2.47–4/mo) + volumes (~$0.15/GB); more DevOps (fly.toml, machines API) | ~$3–5/mo |
| Vercel | **Rejected.** Serverless functions: ephemeral filesystem, no background daemon threads (the sweep scheduler is a thread), no persistent SQLite/fcntl. Would force a rebuild around external state (Neon/Supabase) + cron + blob storage | n/a — wrong shape |
| Cloudflare Quick Tunnels (`scripts/public_tunnel.sh`) | Demo-only: public URL for a process on this machine; URL is ephemeral, box must stay awake | free |
| Cloudflare Workers | **Rejected as a port**: request-scoped Python runtime (Pyodide) — no `fcntl` file locks, no daemon threads, no POSIX filesystem; the append-only audit trail would have to be redesigned onto Durable Objects/R2. Free plan CPU budget (10ms) also cannot run an 85-store sweep | rewrite |
| Hugging Face Spaces | Free Docker demo hosting; sleeps on free; weak ops story | free (sleeps) |
| AWS/GCP/Azure containers | The production answer at real-retailer scale; overkill for a portfolio, revisit on adoption | varies |

There is no "retail agent host" norm to copy — the audit-trail requirement is
this system's differentiator, and a persistent disk is the cheapest way to
keep it. Render Starter + disk is the chosen posture: cheapest always-on
durable deployment with the least new machinery.
