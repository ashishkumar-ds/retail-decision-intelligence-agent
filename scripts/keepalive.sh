#!/usr/bin/env bash
# Keep-alive for the Render free-tier services (forecast API + campaign audit API
# + this decision agent itself).
#
# Render spins down free services after ~15 min of inactivity; a cold start
# takes 30-60s. This script pings the sibling APIs always, and the agent too
# when AGENT_URL is set - it spins down like everything else, so a demo URL or
# a scheduled sweep would otherwise hit a cold start or look dead.
#
# Usage:
#   ./scripts/keepalive.sh                     # sibling APIs only
#   AGENT_URL=https://<service>.onrender.com ./scripts/keepalive.sh     # + agent
#   FORECAST_API_URL=... AUDIT_API_URL=... ./scripts/keepalive.sh       # overrides
#
# Schedule it with cron (every 10 min is safe against the 15-min spin-down):
#   */10 * * * * /path/to/retail-decision-intelligence-agent/scripts/keepalive.sh >> /tmp/keepalive.log 2>&1
set -u

FORECAST_URL="${FORECAST_API_URL:-https://retail-forecast-api-7sue.onrender.com/}"
AUDIT_URL="${AUDIT_API_URL:-https://retail-campaign-automation.onrender.com/audit}"
AGENT_URL="${AGENT_URL:-}"
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
if [ -n "$AGENT_URL" ]; then
    AGENT_URL="${AGENT_URL%%/}"
    ping decision-agent "$AGENT_URL/health"
else
    echo "$(date -u +%FT%TZ) [keepalive] decision-agent skipped (AGENT_URL unset)"
fi
