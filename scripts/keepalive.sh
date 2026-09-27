#!/usr/bin/env bash
# Keep-alive for the Render free-tier services (forecast API + campaign audit API
# + this decision agent itself).
#
# Render spins down free services after ~15 min of inactivity; a cold start
# takes 30-60s. This script pings the sibling APIs always, and the agent too
# when AGENT_URL is set - a free-tier demo instance or a scheduled sweep would
# otherwise hit a cold start or look dead.
#
# Why it classifies the response instead of just passing -f:
#   * 2xx/3xx - warm, done;
#   * 429 - the service *answered*, so the keep-alive goal (an awake instance)
#     is already met; the host is throttling the caller. Counted as success and
#     said out loud, because a silent 429 is invisible otherwise;
#   * 5xx or no response - a cold start in progress or a transient failure, so
#     retry with backoff (ATTEMPTS times, BACKOFF seconds apart);
#   * any other 4xx - not transient. It is a wiring error: a wrong path, or a
#     base URL that already ends in "/" producing "//health". Retrying cannot
#     fix it, so fail immediately and print the URL.
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
TIMEOUT="${KEEPALIVE_TIMEOUT:-120}"     # long enough to absorb a cold start
ATTEMPTS="${KEEPALIVE_ATTEMPTS:-3}"
BACKOFF="${KEEPALIVE_BACKOFF:-10}"      # seconds; multiplied by the attempt number
# Strip trailing slashes so appending "/health" never produces a double slash:
# "//health" is a 404 on this API, not a redirect, so the ping would fail while
# looking like a healthy configuration.
FORECAST_URL="${FORECAST_URL%%/}"
AUDIT_URL="${AUDIT_URL%%/}"

ping() {
    local name="$1" url="$2" attempt=1 code
    while true; do
        code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" "$url" 2>/dev/null)" || code="000"
        case "$code" in
            2??|3??)
                echo "$(date -u +%FT%TZ) [keepalive] $name OK (HTTP $code)"
                return 0 ;;
            429)
                echo "$(date -u +%FT%TZ) [keepalive] $name awake, caller throttled (HTTP 429)"
                return 0 ;;
            000|5??)
                if [ "$attempt" -ge "$ATTEMPTS" ]; then
                    break
                fi ;;
            *)
                break ;;   # a 4xx that is not throttling: wiring, not weather
        esac
        echo "$(date -u +%FT%TZ) [keepalive] $name retry $attempt/$ATTEMPTS (HTTP $code)"
        sleep "$(( BACKOFF * attempt ))"
        attempt=$(( attempt + 1 ))
    done
    echo "$(date -u +%FT%TZ) [keepalive] $name FAILED (HTTP $code, $url)"
    return 1
}

failed=0
ping forecast "$FORECAST_URL/health" || failed=1
ping campaign-audit "$AUDIT_URL" || failed=1
if [ -n "$AGENT_URL" ]; then
    AGENT_URL="${AGENT_URL%%/}"
    ping decision-agent "$AGENT_URL/health" || failed=1
else
    echo "$(date -u +%FT%TZ) [keepalive] decision-agent skipped (AGENT_URL unset)"
fi
# Exit non-zero if any target is not warm: the whole point of a scheduled ping
# is to be a signal, and the previous shape returned the status of whichever
# ping ran last.
exit "$failed"

