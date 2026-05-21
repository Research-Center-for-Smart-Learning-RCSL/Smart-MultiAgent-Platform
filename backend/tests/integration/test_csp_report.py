"""`/api/csp-report` ingress: capped body, graceful non-JSON (API-10).

Mounts only the csp_report router on a throwaway FastAPI app so the test is
independent of DB/Redis and of the broader middleware stack.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import csp_report


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(csp_report.router)
    return TestClient(app)


def test_small_report_accepted() -> None:
    with _client() as c:
        r = c.post(
            "/api/csp-report",
            json={"csp-report": {"violated-directive": "script-src 'self'"}},
        )
    assert r.status_code == 204


def test_non_json_body_accepted() -> None:
    # Browsers send slightly different shapes; a non-JSON body must not 500.
    with _client() as c:
        r = c.post("/api/csp-report", content=b"not json at all")
    assert r.status_code == 204


def test_oversized_report_rejected_via_content_length() -> None:
    # httpx sets Content-Length from the payload; the cheap early-out fires.
    payload = b'{"x":"' + b"A" * (32 * 1024) + b'"}'
    with _client() as c:
        r = c.post("/api/csp-report", content=payload)
    assert r.status_code == 413


def test_oversized_chunked_report_rejected() -> None:
    # An iterator body makes httpx use chunked transfer (no Content-Length),
    # so only the streaming byte counter can reject it.
    def _chunks() -> Iterator[bytes]:
        for _ in range(64):
            yield b"A" * 1024

    with _client() as c:
        r = c.post("/api/csp-report", content=_chunks())
    assert r.status_code == 413
