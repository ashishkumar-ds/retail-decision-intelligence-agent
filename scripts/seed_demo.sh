#!/usr/bin/env bash
# One-command demo seeding: start the agent with demo-safe settings and run
# one recommendation sweep so the showcase state is fresh and deterministic.
#
#   bash scripts/seed_demo.sh
#
# Safe for demos: uses the local .env LLM layers if present, a demo-scoped
# auth token (NOT for production), and never touches GitHub or real spend.
set -euo pipefail
cd "$(dirname "$0")/.."

# Optional local secrets (free Groq key etc.). Never printed, never committed.
if [ -f .env.local ]; then set -a; source .env.local; set +a; fi

export APPROVAL_AUTH_TOKEN="${APPROVAL_AUTH_TOKEN:-demo-token-not-for-production}"
export PYTHONPATH=.

echo "== starting agent on :8001 =="
pkill -f 'uvicorn app.main:app' 2>/dev/null || true
sleep 1
setsid nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8001 > /tmp/demo_agent.log 2>&1 < /dev/null &
for _ in $(seq 1 20); do
  curl -sf localhost:8001/health > /dev/null && break
  sleep 1
done
curl -sf localhost:8001/health > /dev/null || { echo "agent failed to start"; tail /tmp/demo_agent.log; exit 1; }

echo "== running one recommendation sweep =="
# The sweep consumes Project 2's campaign audit log (store_ids per run).
# Point CAMPAIGN_AUDIT_LOG_PATH at it (env) when available; otherwise the
# demo runs from the persisted recommendation log, which is read-only-safe.
if [ -n "${CAMPAIGN_AUDIT_LOG_PATH:-}" ] && [ -f "${CAMPAIGN_AUDIT_LOG_PATH}" ]; then
  curl -s -X POST localhost:8001/recommendations/run \
    -H "Authorization: Bearer $APPROVAL_AUTH_TOKEN" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if "detail" in d:
    print("sweep skipped:", d["detail"][:100])
else:
    print("sweep completed:", d)
'
else
  echo "sweep skipped: set CAMPAIGN_AUDIT_LOG_PATH to Project 2's audit_log.jsonl to enable"
  echo "              (demo runs from the persisted append-only recommendation log)"
fi

echo "== demo state summary =="
curl -s localhost:8001/board | python3 -c '
import json, sys
d = json.load(sys.stdin)
c = d["counts"]
print("stores: %s | needs_intervention: %s | working_well: %s | recovering: %s | watch: %s" % (
    d["total_stores"], c["needs_intervention"], c["working_well"], c["recovering"], c["watch"]))
'
echo
echo "Demo is ready. Walkthrough: DEMO.md   Dashboard: http://localhost:8001/ui"
