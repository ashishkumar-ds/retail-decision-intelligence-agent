#!/usr/bin/env bash
# Verify a deployed agent against this repo's contract.
#
# Every check here exists because the corresponding mistake actually happened:
#   * the live service ran with SWEEP_ENABLED unset, so the autonomous sweep was
#     off while /health looked healthy;
#   * a scheduled workflow pinged a URL that 404'd and still reported success;
#   * the deploy silently lagged behind main after a fix landed.
# A hand-created Render service has no blueprint validating it, so this is the
# post-deploy gate: run it after the first deploy and after every env change.
#
# Usage:
#   bash scripts/verify_deployment.sh https://<service>.onrender.com [APPROVAL_TOKEN]
#
# Warnings are informational (a deliberate posture is still a posture);
# failures mean "do not share this URL yet". Exits 1 if anything failed.
set -u

BASE="${1:-}"
TOKEN="${2:-}"
while [ "${BASE%/}" != "$BASE" ]; do BASE="${BASE%/}"; done   # tolerate trailing slashes
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT

if [ -z "$BASE" ]; then
    echo "usage: bash scripts/verify_deployment.sh https://<service>.onrender.com [APPROVAL_TOKEN]" >&2
    exit 2
fi

failures=0
warnings=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; warnings=$((warnings + 1)); }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; failures=$((failures + 1)); }
head2() { printf '\n%s\n' "$1"; }
status() {  # status URL [extra curl args...] -> HTTP code on stdout
    local url="$1"; shift
    curl -s -o "$BODY" -w '%{http_code}' --max-time 90 "$@" "$url" 2>/dev/null || echo "000"
}
json() {  # json features | scheduler_enabled | route_operations  (reads $BODY)
    python3 - "$BODY" "$1" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
what = sys.argv[2]
if what == "features":
    print(data.get("features"))
elif what == "scheduler_enabled":
    print(data.get("scheduler", {}).get("enabled"))
elif what == "route_operations":
    print(sum(len([m for m in methods if m in ("get", "post")])
              for methods in data.get("paths", {}).values()))
PY
}

head2 "1. Liveness (a cold start can take ~60s)"
code="$(status "$BASE/health")"
if [ "$code" = "200" ]; then
    pass "/health answered 200"
    echo "       features=$(json features)"
    case "$(json scheduler_enabled)" in
        True)  pass "the autonomous sweep is enabled on this service" ;;
        False) warn "SWEEP_ENABLED is not set here, so the daily sweep is OFF. Set SWEEP_ENABLED=1 (and SWEEP_INTERVAL_SECONDS) in the Render dashboard to turn it on." ;;
        *)     warn "could not read the scheduler state from /health" ;;
    esac
else
    fail "/health answered $code (expected 200; check the Render Events/Logs tab)"
fi

head2 "2. The deploy matches this repo (route drift)"
deployed_ops="$(status "$BASE/openapi.json" >/dev/null; json route_operations)"
local_ops="$(grep -cE '^@app\.(get|post)\(' "$ROOT/app/main.py" 2>/dev/null || echo 0)"
if [ -n "$deployed_ops" ] && [ "$deployed_ops" = "$local_ops" ]; then
    pass "deployed route count matches main ($local_ops operations)"
elif [ -n "$deployed_ops" ]; then
    fail "deployed serves $deployed_ops operations vs $local_ops on main - this service is behind (or ahead of) the repo"
else
    warn "could not read /openapi.json to compare routes"
fi

head2 "3. Read-only stakeholder surface"
code="$(status "$BASE/board")"
if [ "$code" = "200" ]; then pass "/board answered 200"; else fail "/board answered $code (expected 200)"; fi

head2 "4. Write paths fail closed without a credential"
code="$(status "$BASE/metrics")"
case "$code" in
    401) pass "/metrics answered 401 (credential configured, header missing)" ;;
    503) warn "/metrics answered 503: no APPROVAL_AUTH_TOKEN is set, so every write path is refused (read-only service)" ;;
    *)   fail "/metrics answered $code (expected 401, or 503 when no credential is configured)" ;;
esac
code="$(status "$BASE/approve/1" -X POST)"
case "$code" in
    401) pass "POST /approve answered 401 without a token (the human gate is armed)" ;;
    503) warn "POST /approve answered 503: writes are disabled - set APPROVAL_AUTH_TOKEN to enable the loop" ;;
    *)   fail "POST /approve answered $code without a token - a write path may be reachable unauthenticated" ;;
esac

head2 "5. Authenticated surfaces (only if a token was supplied)"
if [ -n "$TOKEN" ]; then
    code="$(status "$BASE/metrics" -H "Authorization: Bearer $TOKEN")"
    if [ "$code" = "200" ] && grep -q 'retail_decisions_total' "$BODY"; then
        pass "/metrics answered 200 with the decision counters"
    else
        fail "/metrics answered $code with the token (expected 200 + retail_decisions_total)"
    fi
    code="$(status "$BASE/metrics" -H "Authorization: Bearer definitely-wrong-token")"
    if [ "$code" = "401" ]; then pass "a wrong token is rejected (401)"; else fail "a wrong token answered $code (expected 401)"; fi
else
    warn "no token supplied - the authenticated checks were skipped (pass it as the 2nd argument)"
fi

printf '\nSummary: %s failure(s), %s warning(s) against %s\n' "$failures" "$warnings" "$BASE"
[ "$failures" -eq 0 ] || exit 1

