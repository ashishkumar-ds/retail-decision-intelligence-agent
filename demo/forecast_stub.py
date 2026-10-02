#!/usr/bin/env python3
"""Playback stand-in for the Project 1 forecast API (demo only; stdlib only).

Why this exists: the agent reads every store signal from the deployed forecast
service, so a fresh clone cannot show anything without a network round-trip to
someone else's instance. This module replays REAL responses recorded from that
service - ``/health``, ``/stores``, ``/predict``, ``/controls/{id}``,
``/actuals/{id}`` - from ``demo/recordings.json`` (captured 2026-10-02), so
``scripts/seed_demo.sh`` runs the whole agent offline with real numbers: no API
keys, no Render service, no Project 2.

It is a recording, not a model: every number it serves is a byte the real
service once returned. It is never imported by the runtime - the agent reaches
it over HTTP via ``FORECAST_API_URL``, the same seam a local forecast instance
already used, so no production code path changes.

Usage:
    python3 demo/forecast_stub.py --port 8097 [--recordings demo/recordings.json]
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_RECORDINGS = Path(__file__).resolve().parent / "recordings.json"


def _load_recordings(path: Path) -> dict:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    return {
        "predictions": bundle["predictions"],
        "controls": bundle["controls"],
        "actuals": bundle["actuals"],
        "last_day": {int(store_id): last for store_id, last in bundle["last_day"].items()},
    }


_DATA = _load_recordings(_RECORDINGS)
_STORE_IDS = sorted(int(store_id) for store_id in _DATA["last_day"])


def _predict(store_id: int, day: int) -> float:
    """Replay the recorded prediction, the verbatim real-service byte."""
    key = f"{store_id}/{day}"
    if key not in _DATA["predictions"]:
        raise KeyError(f"no recorded prediction for store {store_id} day {day}")
    return float(_DATA["predictions"][key])


def _stores_payload() -> dict:
    return {"stores": [{"store_id": store_id, "last_day": _DATA["last_day"][store_id]}
                       for store_id in _STORE_IDS]}


def _controls_payload(store_id: int) -> dict:
    key = str(store_id)
    if key not in _DATA["controls"]:
        raise KeyError(f"no recorded controls for store {store_id}")
    return _DATA["controls"][key]


def _actuals_payload(store_id: int, query: dict) -> dict:
    key = str(store_id)
    if key not in _DATA["actuals"]:
        raise KeyError(f"no recorded actuals for store {store_id}")
    recorded = _DATA["actuals"][key]
    start_day = int(query.get("start_day", [str(recorded["start_day"])])[0])
    end_day = int(query.get("end_day", [str(recorded["end_day"])])[0])
    observations = [obs for obs in recorded["observations"]
                    if start_day <= obs["day"] <= end_day]
    return {
        "store_id": store_id,
        "start_day": start_day,
        "end_day": end_day,
        "range_start_date": recorded["range_start_date"],
        "range_end_date": recorded["range_end_date"],
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
            return self._send({"status": "ok", "stores_available": len(_STORE_IDS),
                               "mode": "demo-playback"})
        if parsed.path == "/stores":
            return self._send(_stores_payload())
        if len(parts) == 2 and parts[0] in ("controls", "actuals"):
            try:
                store_id = int(parts[1])
                payload = (_controls_payload(store_id) if parts[0] == "controls"
                           else _actuals_payload(store_id, parse_qs(parsed.query)))
            except (KeyError, ValueError):
                return self._send({"detail": "outside the recorded demo universe"}, status=404)
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
            value = _predict(store_id, day)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._send({"detail": "store_id and day are required integers"}, status=422)
        self._send({"store_id": store_id, "day": day, "predicted_sales_value": value})


def main() -> None:
    parser = argparse.ArgumentParser(description="Local forecast-API playback for the demo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8097)
    parser.add_argument("--recordings", type=Path, default=_RECORDINGS,
                        help="recorded real-service bundle (see demo/recordings.json)")
    args = parser.parse_args()

    global _DATA, _STORE_IDS  # noqa: PLW0603 - one deliberate load per process start
    _DATA = _load_recordings(args.recordings)
    _STORE_IDS = sorted(int(store_id) for store_id in _DATA["last_day"])

    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"demo forecast playback on http://{args.host}:{args.port} "
          f"({len(_STORE_IDS)} real recorded stores)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()