#!/usr/bin/env bash
# One-command demo seeding: run the agent against LOCAL fixtures so a fresh
# clone shows a working portfolio with no API keys and no Render services.
#
#   bash scripts/seed_demo.sh
#
# What it wires - each only when you have NOT set it yourself, so a real
# deployment configuration is never silently overwritten:
#   CAMPAIGN_AUDIT_LOG_PATH -> a copy of demo/campaign_audit.jsonl (store universe)
#   FORECAST_API_URL        -> demo/forecast_stub.py             (store signals)
#
# The stub and the fixture are demo stand-ins, not real data - see demo/README.md.
# Safe for demos: a demo-scoped auth token (NOT for production), and it never
# touches GitHub or real spend.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# Optional local secrets (free Groq key etc.). Never printed, never committed.
if [ -f .env.local ]; then set -a; source .env.local; set +a; fi

export APPROVAL_AUTH_TOKEN="${APPROVAL_AUTH_TOKEN:-demo-token-not-for-production}"
export PYTHONPATH=.

PYTHON="$(command -v python3 || command -v python)"

# What the operator already had before this script defaults anything. The
# closing summary must describe the run that actually happened: fully local
# only when nothing external was in play. A localhost URL is local tooling,
# not an external service.
HAS_FORECAST_URL="${FORECAST_API_URL:-}"
HAS_AUDIT_URL="${CAMPAIGN_AUDIT_API_URL:-}"
_is_remote() { case "$1" in *localhost*|*127.0.0.1*|"") return 1;; *) return 0;; esac; }

DEMO_STATE="$(mktemp -d)"
STUB_PID=""
cleanup() {
  [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null || true
  rm -rf "$DEMO_STATE"
}
trap cleanup EXIT

# --- Store universe: the bundled fixture, with a refreshed start date --------
# run_timestamp drives days_elapsed, which drives the whole recommendation. A
# fixed date in the file would drift until the demo read "0 days left" and every
# store escalated. Seed a ~20-day-old run instead, so the demo keeps showing the
# interesting middle of the 60-day recovery window.
AUDIT_FIXTURE="$ROOT/demo/campaign_audit.jsonl"
DEMO_AUDIT="$DEMO_STATE/campaign_audit.jsonl"
"$PYTHON" - "$AUDIT_FIXTURE" "$DEMO_AUDIT" <<'PY'
import datetime, json, sys
source, target = sys.argv[1], sys.argv[2]
started = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=20)
with open(source, encoding="utf-8") as handle, open(target, "w", encoding="utf-8") as out:
    for line in handle:
        if line.strip():
            run = json.loads(line)
            run["run_timestamp"] = started.isoformat()
            out.write(json.dumps(run) + "\n")
PY
export CAMPAIGN_AUDIT_LOG_PATH="${CAMPAIGN_AUDIT_LOG_PATH:-$DEMO_AUDIT}"

# --- Store signals: local stub unless a forecast service is already configured
# Standard demo practice: the stub dies when this script does. It is a demo
# aid, not a service - a second run can never fight a leftover stub over the
# port, and production never sees either of them.
if [ -z "${FORECAST_API_URL:-}" ]; then
  STUB_PORT="${DEMO_FORECAST_PORT:-8097}"
  echo "== starting the local forecast stub on :$STUB_PORT =="
  nohup "$PYTHON" demo/forecast_stub.py --port "$STUB_PORT" > "$DEMO_STATE/stub.log" 2>&1 &
  STUB_PID=$!
  for _ in $(seq 1 20); do
    curl -sf "localhost:$STUB_PORT/health" > /dev/null && break
    sleep 1
  done
  curl -sf "localhost:$STUB_PORT/health" > /dev/null || {
    echo "forecast stub failed to start:"; cat "$DEMO_STATE/stub.log"; exit 1; }
  export FORECAST_API_URL="http://127.0.0.1:$STUB_PORT/"
else
  echo "== using the configured FORECAST_API_URL=$FORECAST_API_URL =="
fi

echo "== starting the agent on :8001 =="
for pid in $(ps -eo pid,args 2>/dev/null | grep 'app.main:app' | grep -v grep | awk '{print $1}'); do
  kill "$pid" 2>/dev/null || true
done
sleep 1
setsid nohup "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port 8001 > /tmp/demo_agent.log 2>&1 < /dev/null &
for _ in $(seq 1 20); do
  curl -sf localhost:8001/health > /dev/null && break
  sleep 1
done
curl -sf localhost:8001/health > /dev/null || { echo "agent failed to start"; tail /tmp/demo_agent.log; exit 1; }

echo "== running one recommendation sweep =="
curl -s -X POST localhost:8001/recommendations/run \
  -H "Authorization: Bearer $APPROVAL_AUTH_TOKEN" | "$PYTHON" -c '
import json, sys
d = json.load(sys.stdin)
if "detail" in d:
    print("sweep skipped:", d["detail"][:120])
else:
    print("sweep completed: evaluated %s store(s)" % d.get("total_stores_evaluated"))
'

echo "== demo state summary =="
curl -s localhost:8001/board | "$PYTHON" -c '
import json, sys
d = json.load(sys.stdin)
c = d["counts"]
print("stores: %s | needs_intervention: %s | working_well: %s | recovering: %s | watch: %s" % (
    d["total_stores"], c["needs_intervention"], c["working_well"], c["recovering"], c["watch"]))
'
echo
USE_LOCAL_AUDIT=1
if _is_remote "$HAS_AUDIT_URL"; then USE_LOCAL_AUDIT=0; fi
USE_LOCAL_FORECAST=1
if _is_remote "$HAS_FORECAST_URL"; then USE_LOCAL_FORECAST=0; fi
# ponytail: the "no external services" banner stays conditional in the demo
# entry point (the script owns its claim); no decision-path code changes for
# a demo.
if [ "$USE_LOCAL_AUDIT" = 1 ] && [ "$USE_LOCAL_FORECAST" = 1 ]; then
  echo "Demo is ready - no external services were needed."
else
  echo "Demo is ready - served with external services:"
  _is_remote "$HAS_FORECAST_URL" && echo "  forecast: $HAS_FORECAST_URL"
  _is_remote "$HAS_AUDIT_URL" && echo "  campaign audit: $HAS_AUDIT_URL"
fi
echo "  Board API:   http://localhost:8001/board"
echo "  Live board:  https://retail-decision-intelligence-agent.vercel.app/"
echo "  Walkthrough: DEMO.md"
