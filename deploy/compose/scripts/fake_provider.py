"""Fake LLM provider for the E2E stack — TEST ONLY, never deploy this.

The key-upload flow runs a live probe against the provider before recording a
key's `test_status` (R7.02). CI has no real provider secret, so without an
answering endpoint every seeded key lands as `failed` and the E2E fixtures
cannot represent a healthy key.

This serves the exact request shapes the probes in
`backend/contexts/keys/infrastructure/probes/` send, and is reached only
because `compose.test.yml` points `SMAP_PROBE_*_BASE_URL` here. The probe code
itself is unchanged and still runs in full: a request with no credential still
gets 401, so the seed proves the round-trip really happened.

Stdlib only — runs on a bare `python:3.12-alpine` with no install step.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PORT = 8080

# Path -> canned success body, mirroring each probe's expected 200 shape.
_GET_ROUTES: dict[str, dict[str, Any]] = {
    # openai / cohere / gemini all probe GET /v1/models
    "/v1/models": {
        "object": "list",
        "data": [{"id": "fake-model", "object": "model"}],
        "models": [{"name": "models/fake-model"}],
    },
}
_POST_ROUTES: dict[str, dict[str, Any]] = {
    # anthropic probes POST /v1/messages with max_tokens=1
    "/v1/messages": {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": "fake-model",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "max_tokens",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    },
    # voyage probes POST /v1/embeddings
    "/v1/embeddings": {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.0, 0.0, 0.0]}],
        "model": "fake-model",
        "usage": {"total_tokens": 1},
    },
}

_CREDENTIAL_HEADERS = ("authorization", "x-api-key", "x-goog-api-key")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _has_credential(self) -> bool:
        """Any non-empty provider credential header.

        The value is never inspected, compared, or logged — this is not an
        auth check, it only keeps the fake from rubber-stamping a request the
        real provider would have rejected outright.
        """
        for name in _CREDENTIAL_HEADERS:
            value = (self.headers.get(name) or "").strip()
            if value and value.lower() not in ("bearer", "bearer "):
                return True
        return False

    def _handle(self, routes: dict[str, dict[str, Any]]) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send(200, {"status": "ok"})
            return
        if path not in routes:
            self._send(404, {"error": {"type": "not_found"}})
            return
        if not self._has_credential():
            self._send(401, {"error": {"type": "authentication_error"}})
            return
        self._send(200, routes[path])

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        self._handle(_GET_ROUTES)

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        length = int(self.headers.get("content-length") or 0)
        if length:
            self.rfile.read(length)
        self._handle(_POST_ROUTES)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silence the default access log.

        Probe requests carry the secret in a header, and the default handler
        logs the request line plus assorted metadata. Nothing about a fake
        provider is worth the risk of a credential reaching a CI log.
        """
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104
