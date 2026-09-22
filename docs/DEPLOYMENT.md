# Deployment

The image is standard and boring: `python:3.12-slim`, non-root user, deps
pinned, one uvicorn process bound to `0.0.0.0:$PORT`. `render.yaml` is the
blueprint; `docker compose up -d --build` is the same thing locally.

```bash
docker compose up -d --build          # local
curl http://localhost:8001/health
```

## Render (the stable stakeholder URL)

Nothing is needed from any third party beyond **your own** Render account -
no credentials are passed around, and no secrets live in the repo. The whole
deploy is a click-through:

1. Render dashboard → **New → Blueprint** → connect this GitHub repo.
   Render reads `render.yaml` and proposes the service.
2. Fill the variables marked `sync: false` in the dashboard (never in git):
   - `APPROVAL_AUTH_TOKEN` - **required for any write path.** While unset the
     service is read-only by design (approve/reject/execute return 503), so a
     first deploy is safe even before you set it.
   - `APPROVAL_TOKENS` - optional named approvers (`tok:user:role`) with roles
     `approver`/`viewer`; supersedes the shared token and records `decided_by`.
   - `FORECAST_API_URL` - the deployed forecast service to read actuals from.
   - `DATABASE_URL` - optional `postgres://` URL for the pending-approval
     store.
   - `LLM_*` / `ANTHROPIC_API_KEY` - optional, only if you want the LLM layers.
3. Deploy. First boot runs the schema migrations (`storage/database.py`) and
   rebuilds the RAG corpus from `rag/sources/`.

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
| Paid instance + the `disk:` block in `render.yaml` (as shipped) | The correct production posture: the ledger survives deploys. Trade-off: disks pin the service to one instance and disable zero-downtime deploys. | ~$7/mo instance + disk |
| Free tier, `disk:` block deleted | A **demo URL**. Works completely, but state resets on every deploy/restart - say so out loud when you share it. | free |
| Migrate the JSONL stores to Postgres tables | Durability without a disk, multi-instance safe. Open work: `memory/history.py` is a pinned carve-out (append-only, fsynced, never rewritten), so it must move with its golden cases in the same commit. | engineering time |

`DATABASE_URL` alone does **not** solve this: it makes the pending-approval
store durable, not the JSONL logs.

### Post-deploy verification (5 minutes)

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
  section are the only surfaces that leave the process. The analytics section
  is off unless `ROOT_CAUSE_TAGGING_ENABLED=true`, and reason text is
  numerically redacted before it is sent.
- **Rollback**: Render keeps previous deploys, but the *state* is the disk -
  rolling back code without the matching disk loses decisions recorded after
  it.
