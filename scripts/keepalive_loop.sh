#!/usr/bin/env bash
# Continuous keep-alive loop for the Render free-tier APIs.
# Unlike cron (not available on all hosts), this is self-contained: it pings
# both services every INTERVAL seconds and can run in the background:
#
#   ./scripts/keepalive_loop.sh &            # runs until killed
#   nohup ./scripts/keepalive_loop.sh > /tmp/keepalive_loop.log 2>&1 &
#
# Config via env (defaults are the deployed endpoints / 10-minute interval).
set -u

INTERVAL="${KEEPALIVE_INTERVAL:-600}"          # seconds; 600 = every 10 min
FORECAST_URL="${FORECAST_API_URL:-https://retail-forecast-api-7sue.onrender.com/}"
AUDIT_URL="${AUDIT_API_URL:-https://retail-campaign-automation.onrender.com/audit}"
TIMEOUT="${KEEPALIVE_TIMEOUT:-180}"            # long enough to absorb a cold start
# Strip trailing slashes so appending "/health" never produces a double slash.
FORECAST_URL="${FORECAST_URL%%/}"
AUDIT_URL="${AUDIT_URL%%/}"

log() { echo "$(date -u +%FT%TZ) $*"; }

ping() {
    local name="$1" url="$2"
    if curl -fsS --max-time "$TIMEOUT" "$url" > /dev/null 2>&1; then
        log "[keepalive-$name] OK"
    else
        log "[keepalive-$name] FAILED ($url)"
    fi
}

log "keep-alive loop started (interval=${INTERVAL}s, timeout=${TIMEOUT}s)"
while true; do
    ping forecast "$FORECAST_URL/health"
    ping audit "$AUDIT_URL"
    sleep "$INTERVAL"
done