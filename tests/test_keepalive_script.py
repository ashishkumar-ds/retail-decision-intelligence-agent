"""The keep-alive script's response classification is load-bearing.

A scheduled pinger that says "OK" while asking for a URL that does not exist is
worse than no pinger: the Render keep-alive workflow sat red for weeks building
``…onrender.com//health`` (a 404) and never warmed anything. So the rules are
pinned here, against a local stub server - no network, no Render:

- one slash, always (a trailing slash in the base URL must not double up);
- 2xx/3xx - warm;
- 429 - the service answered, so it is awake; the caller is being throttled and
  that is not a failure;
- 5xx / no response - retry with backoff, then fail;
- any other 4xx - fail immediately: a wrong path is wiring, not weather.

``bash`` and ``curl`` are required; skip where they are absent.
"""
from __future__ import annotations

import http.server
import os
import pathlib
import shutil
import subprocess
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "keepalive.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("curl") is None,
                                reason="keepalive.sh needs bash and curl")


@pytest.fixture
def serve():
    """Start stub servers that answer a fixed status; returns url, paths, stop."""
    servers: list[http.server.ThreadingHTTPServer] = []

    def _start(status: int) -> tuple[str, list[str]]:
        request_paths: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server's API
                request_paths.append(self.path)
                self.send_response(status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):  # keep the test output clean
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_port}", request_paths

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


def _run(url: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run the script against a stub URL with a minimal, hermetic environment."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "FORECAST_API_URL": url,
        "AUDIT_API_URL": url,
        "KEEPALIVE_TIMEOUT": "5",
    }
    env.update(extra_env or {})
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                          env=env, timeout=60, check=False)


def test_warm_service_succeeds_and_reports_the_status(serve):
    url, paths = serve(200)
    result = _run(url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "forecast OK (HTTP 200)" in result.stdout
    assert "campaign-audit OK (HTTP 200)" in result.stdout
    assert "decision-agent skipped" in result.stdout  # opt-in, unset by default
    assert paths == ["/health", "/"]


def test_trailing_slash_never_doubles(serve):
    """The workflow's bug, pinned: a base URL ending in "/" must not yield "//health"."""
    url, paths = serve(200)
    result = _run(f"{url}/")
    assert result.returncode == 0
    assert "/health" in paths and "//health" not in paths


def test_wrong_path_fails_immediately_without_retrying(serve):
    url, paths = serve(404)
    result = _run(url, {"KEEPALIVE_ATTEMPTS": "3", "KEEPALIVE_BACKOFF": "1"})
    assert result.returncode == 1
    assert "FAILED (HTTP 404" in result.stdout and url in result.stdout
    assert paths == ["/health", "/"]  # one attempt each: retrying cannot fix a path


def test_throttled_caller_counts_as_awake(serve):
    """429 proves the instance is up - which is the entire point of a keep-alive."""
    url, paths = serve(429)
    result = _run(url)
    assert result.returncode == 0, result.stdout
    assert "awake, caller throttled (HTTP 429)" in result.stdout
    assert paths == ["/health", "/"]


def test_cold_start_retries_then_fails(serve):
    url, paths = serve(503)
    result = _run(url, {"KEEPALIVE_ATTEMPTS": "2", "KEEPALIVE_BACKOFF": "1"})
    assert result.returncode == 1
    assert "retry 1/2 (HTTP 503)" in result.stdout
    assert "FAILED (HTTP 503" in result.stdout
    assert paths == ["/health", "/health", "/", "/"]  # retried, then gave up


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
