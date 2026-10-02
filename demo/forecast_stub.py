#!/usr/bin/env python3
"""Local stand-in for the Project 1 forecast API (demo only; standard library only).

Why this exists: the agent reads every store signal from the deployed forecast
service, so a fresh clone cannot show anything without a network round-trip to
someone else's instance. This module serves the SAME contract locally -
``/health``, ``/stores``, ``/predict``, ``/controls/{id}``, ``/actuals/{id}`` -
from a small fixed table, so ``scripts/seed_demo.sh`` can run the whole agent
offline: no API keys, no Render service, no Project 2.

It is a stub, not a model. The numbers are fixed per store and deliberately
not predictions; the decision path treats them exactly as it treats the real
feed. It is never imported by the runtime - the agent reaches it over HTTP via
``FORECAST_API_URL``, the same seam a local forecast instance already used, so
no production code path changes.

Usage:
    python3 demo/forecast_stub.py --port 8097
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# store_id -> (baseline daily sales at the store's last observed day, per-day
# growth, last observed day). The growth spread is what makes the demo board
# varied: a recovering store, a flat one, and two underperforming ones.
_STORES: dict[int, tuple[float, float, int]] = {
    31642: (2400.0, 0.0018, 560),
    317: (1750.0, -0.0020, 560),
    299: (3100.0, 0.0005, 560),
    289: (1420.0, -0.0035, 560),
    31582: (2680.0, 0.0030, 560),
}
_FALLBACK = (2000.0, 0.0, 560)

# Part 1's calibrated DiD prior, reused verbatim so the stub's causal envelope
# matches the value the engine already carries in `causal_baseline`.
_CAUSAL_DID_PCT = 2.84
_CAUSAL_CI95 = [-0.5, 6.2]
_EPOCH = date(2024, 1, 1)


def _predict(store_id: int, day: int) -> float:
    """Deterministic daily sales for a store on a day index."""
    base, growth, last_day = _STORES.get(store_id, _FALLBACK)
    return round(base * (1.0 + growth * (day - last_day)), 2)


def _day_date(day: int) -> str:
    return (_EPOCH + timedelta(days=day)).isoformat()


def _stores_payload() -> dict:
    return {"stores": [{"store_id": store_id, "last_day": params[2]}
                       for store_id, params in sorted(_STORES.items())]}


def _controls_payload(store_id: int) -> dict:
    return {
        "store_id": store_id,
        "windows": {"pre_start": 500, "pre_end": 545, "post_start": 547, "post_end": 560},
        "matched_controls": [{"store_id": 9001, "weight": 0.5},
                             {"store_id": 9002, "weight": 0.5}],
        "causal": {"did_uplift_pct": _CAUSAL_DID_PCT, "ci95_pct": list(_CAUSAL_CI95)},
        "methodology": "demo stub: fixed DiD prior, not a live estimate",
    }


def _actuals_payload(store_id: int, query: dict) -> dict:
    start_day = int(query.get("start_day", ["500"])[0])
    end_day = int(query.get("end_day", [str(start_day)])[0])
    observations = [{"day": day, "date": _day_date(day), "sales_value": _predict(store_id, day)}
                    for day in range(start_day, end_day + 1)]
    return {
        "store_id": store_id,
        "start_day": start_day,
        "end_day": end_day,
        "range_start_date": _day_date(start_day),
        "range_end_date": _day_date(end_day),
        "observation_count": len(observations),
        "observations": observations,
    }
class _Handler(BaseHTTPRequestHandler):
    server_version = "demo-forecast-stub/1.0"

    def log_message(self, *args) -> None:  # keep the demo output readable
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server's API
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.path == "/health":
            return self._send({"status": "ok", "stores_available": len(_STORES)})
        if parsed.path == "/stores":
            return self._send(_stores_payload())
        if len(parts) == 2 and parts[0] in ("controls", "actuals"):
            store_id = int(parts[1])
            payload = (_controls_payload(store_id) if parts[0] == "controls"
                       else _actuals_payload(store_id, parse_qs(parsed.query)))
            return self._send(payload)
        self._send({"detail": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802 - http.server's API
        if urlparse(self.path).path != "/predict":
            return self._send({"detail": "not found"}, status=404)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            store_id = int(body["store_id"])
            day = int(body["day"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._send({"detail": "store_id and day are required integers"}, status=422)
        self._send({"store_id": store_id, "day": day,
                    "predicted_sales_value": _predict(store_id, day)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Local forecast-API stub for the demo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8097)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"demo forecast stub listening on http://{args.host}:{args.port} "
          f"({len(_STORES)} stores)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()