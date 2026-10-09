# Deployment

The image is standard and boring: `python:3.12-slim`, non-root user, deps
pinned, one uvicorn process bound to `0.0.0.0:$PORT`. `docker compose up -d
--build` is the same thing locally.

```bash
docker compose up -d --build          # local
curl http://localhost:8001/health
```

## Render (the stable stakeholder URL)

**No blueprint.** The service is created by hand in the dashboard: there is no
`render.yaml` in this repo and no IaC to drift out of sync with reality. The
consequence is the thing to remember — **whatever you do not set in the
dashboard keeps its code default, silently.** That is not hypothetical: the
production service ran with `SWEEP_ENABLED` unset, so the autonomous sweep was
off while `/health` looked perfectly healthy.

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
| `SWEEP_ENABLED` | **`1`** | Opt-in autonomous sweep. Unset → the daily sweep never runs. This is the one that bit us. |
| `SWEEP_INTERVAL_SECONDS` | `86400` | Sweep cadence (daily). |
| `FORECAST_API_URL` | the forecast service URL | Where actuals/predictions come from; the code default is the deployed Project 1 API. | SLO: single upstream, no ensemble/cache — `ERROR` (feed down) vs `NO_DATA` (business gap) per record on `/board`; outage leaves new stores in `NEEDS_REVIEW`. |
| `CAMPAIGN_AUDIT_API_URL` | **required for the sweep** | Project 2's read-only audit log, and the source of the store universe. Unset with no `CAMPAIGN_AUDIT_LOG_PATH` file → `POST /recommendations/run` answers 400 "No store_ids found in the audit log" and the daily sweep does nothing. |
| `RETAIL_STORE_CONTEXT_URL` | optional | P2 `datasets/stores.csv` for the store margin proxy; unset → code default. Unreachable → layer fails open, decisions unchanged. |
| `RETAIL_SKU_ROLLUP_PATH` | optional | Store×SKU rollup CSV for inventory-aware demand-vs-supply; unset → SKU layer off, decisions unchanged. |
| `DATABASE_URL` | optional | `postgres://` for the pending-approval store; unset → SQLite on the disk. |
| `ROOT_CAUSE_TAGGING_ENABLED` | `false` | The board's root-cause section (third-party egress, redacted before sending). |
| `LLM_*`, `ANTHROPIC_API_KEY` | off | Optional explanation/advisory layers; every failure degrades to the deterministic output. |
| `APPROVAL_TOKENS` | optional | Named approvers with roles; supersedes the shared token and records `decided_by`. |
| `EXECUTION_CONNECTOR` | `dry-run` | Name surfaced in `/health`. Only shipped adapter is dry-run (journals intent, no external write). |

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
- **Alerts**: `ops/prometheus/alerts.yml` ships the four rules worth having
  (decision concentration, sweep failures, sweep staleness, approval backlog);
  `/metrics` is token-gated, so scrape with `authorization: Bearer`.
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
