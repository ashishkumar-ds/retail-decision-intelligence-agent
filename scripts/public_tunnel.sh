#!/bin/sh
# Public demo URL for the agent via a Cloudflare Quick Tunnel (try.cloudflare.com).
#
# This is a DEMO tool, not hosting: the URL is ephemeral (new one every run),
# dies with this process, and your machine is the origin. For a permanent URL
# see docs/DEPLOYMENT.md (Render blueprint or a named Cloudflare Tunnel).
#
# Usage: scripts/public_tunnel.sh [PORT]
# Env:   APPROVAL_AUTH_TOKEN should be set so write paths stay gated (the
#        endpoints fail closed without it, but set it for a real demo).
set -eu
PORT="${1:-8001}"
TUNNEL_BIN="$(command -v cloudflared || echo /tmp/cf-arm64)"

if ! command -v python >/dev/null 2>&1 && ! command -v python3 >/dev/null 2>&1; then
    echo "no python found" >&2; exit 1
fi
PYTHON="$(command -v python3 || command -v python)"

echo "[*] starting the agent on :$PORT (logs: /tmp/agent_uvicorn.log)"
PYTHONPATH=. nohup "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" \
    > /tmp/agent_uvicorn.log 2>&1 &
AGENT_PID=$!
sleep 2
if ! kill -0 "$AGENT_PID" 2>/dev/null; then
    echo "[!] agent failed to start:" >&2; tail -5 /tmp/agent_uvicorn.log >&2; exit 1
fi

echo "[*] opening the quick tunnel"
"$TUNNEL_BIN" tunnel --url "http://127.0.0.1:$PORT" --output json > /tmp/cf_tunnel.json 2>/tmp/cf_tunnel.err &
TUNNEL_PID=$!

URL=""
i=0
while [ $i -lt 20 ]; do
    URL="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' /tmp/cf_tunnel.json 2>/dev/null | head -1 || true)"
    [ -n "$URL" ] && break
    i=$((i + 1)); sleep 1
done

if [ -z "$URL" ]; then
    echo "[!] tunnel did not come up; check /tmp/cf_tunnel.err" >&2
    kill "$AGENT_PID" 2>/dev/null || true
    exit 1
fi

cat <<MSG

==========================================================
  Agent:   http://127.0.0.1:$PORT/health   (local)
  Public:  $URL/health

  DEMO REMINDERS
  - Write paths are 503-fail-closed until APPROVAL_AUTH_TOKEN is set.
  - This URL is EPHEMERAL: it changes on every run and dies when this
    script's processes stop. Not a production URL.
  - Stop everything:  kill $AGENT_PID $TUNNEL_PID
==========================================================
MSG
wait
