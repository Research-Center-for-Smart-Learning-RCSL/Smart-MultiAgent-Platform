"""MCP runtime probe — gVisor availability health-check service (R12.05).

NOTE on naming: the directory is historically `mcp_supervisor` and the
docker-compose service is `mcp-sandbox-supervisor`, but this process does
**not** spawn or supervise sandbox containers. It only probes the host
runtime: it shells out to `docker info --format '{{json .Runtimes}}'` and
reports whether the gVisor `runsc` runtime is registered. Sandbox container
lifecycle is owned by the backend MCP context (see
`contexts/mcp/.../sandbox/*`) which spawns containers directly via the
Docker API.

Wire shape:

- GET /healthz → 200 {"status":"ok"} when the Docker daemon is reachable
  AND the gVisor `runsc` runtime is registered.
- 503 {"status":"error","detail":"..."} when either check fails.

This service holds the Docker socket mount and acts as the readiness gate
for sandbox tool calls. Before any backend service spawns a gVisor
container it should verify that this endpoint is healthy.

Docker socket: /var/run/docker.sock must be bind-mounted into this container.
"""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import time
from typing import Any

# ASYNC-11: /healthz is polled frequently (compose/k8s liveness probes, plus
# the sandbox-readiness gate before every gVisor container spawn). Without a
# cache, each GET shells out a fresh `docker` process; under a wedged Docker
# daemon that is pure wasted load — every probe blocks the full 5 s timeout.
# A short TTL coalesces bursts to at most one real probe per window while
# staying responsive to the daemon recovering.
_CACHE_TTL_SECONDS = 10.0
_cache: tuple[float, bool, str] | None = None


def _check() -> tuple[bool, str]:
    try:
        result = subprocess.run(  # noqa: S603
            ["docker", "info", "--format", "{{json .Runtimes}}"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        return False, "docker CLI not found in PATH"
    except subprocess.TimeoutExpired:
        return False, "docker daemon timed out after 5 s"

    if result.returncode != 0:
        stderr = result.stderr.strip()[:200]
        return False, f"docker info failed (exit {result.returncode}): {stderr}"

    raw = result.stdout.strip()
    try:
        runtimes: dict[str, Any] = json.loads(raw or "{}")
    except ValueError:
        return False, f"unexpected docker info output: {raw[:100]}"

    if "runsc" not in runtimes:
        return (
            False,
            "runsc runtime not registered in Docker — install gVisor on the host"
            " (see docs/operations.md §2.1)",
        )
    return True, "ok"


def _check_cached() -> tuple[bool, str]:
    """Return the runtime probe result, re-probing at most once per TTL (ASYNC-11).

    HTTPServer handles requests serially on a single thread, so this plain
    module-level cache needs no lock.
    """
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1], _cache[2]
    ok, detail = _check()
    _cache = (now, ok, detail)
    return ok, detail


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — stdlib BaseHTTPRequestHandler signature
        if self.path != "/healthz":
            self.send_response(404)
            self.end_headers()
            return
        ok, detail = _check_cached()
        body = json.dumps({"status": "ok" if ok else "error", "detail": detail}).encode()
        self.send_response(200 if ok else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # suppress Apache-style access log; structured logs from container stdout are enough


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9090
    server = http.server.HTTPServer(("0.0.0.0", port), _Handler)  # noqa: S104 — supervisor binds in-container only
    server.serve_forever()
