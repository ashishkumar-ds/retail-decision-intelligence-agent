#!/usr/bin/env bash
# Keep-alive for the Render free-tier services (forecast API + campaign audit API).
#
# Render spins down free services after ~15 min of inactivity; a cold start
# takes 30-60s. This script pings both services so they stay warm.
#
# Usage:
#   ./scripts/keepalive.sh                     # uses the default deployed URLs
#   FORECAST_API_URL=... AUDIT_API_URL=... ./scripts/keepalive.sh
#
# Schedule it with cron (every 10 min is safe against the 15-min spin-down):
#   */10 * * * * /path/to/retail-decision-intelligence-agent-v4/scripts/keepalive.sh >> /tmp/keepalive.log 2>&1
set -u

FORECAST_URL="${FORECAST_API_URL:-https://retail-forecast-api-7sue.onrender.com/}"
AUDIT_URL="${AUDIT_API_URL:-https://retail-campaign-automation.onrender.com/audit}"
TIMEOUT="${KEEPALIVE_TIMEOUT:-120}"   # long enough to absorb a cold start
# Strip trailing slashes so appending "/health" never produces a double slash.
FORECAST_URL="${FORECAST_URL%%/}"
AUDIT_URL="${AUDIT_URL%%/}"

ping() {
    local name="$1" url="$2"
    if curl -fsS --max-time "$TIMEOUT" "$url" > /dev/null 2>&1; then
        echo "$(date -u +%FT%TZ) [keepalive] $name OK"
    else
        echo "$(date -u +%FT%TZ) [keepalive] $name FAILED ($url)"
        return 1
    fi
}

ping forecast "$FORECAST_URL/health"
ping campaign-audit "$AUDIT_URL"
