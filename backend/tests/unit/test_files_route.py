"""`GET|HEAD /api/files/{ticket}` streams one stored object ([R19a.14]).

Dossier: docs/tasks/2026-10-11-browser-file-delivery/spec.md §7.3, §7.4, §8.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middleware.security_headers import SecurityHeadersMiddleware
from app.api.v1 import files as files_route
from shared_kernel.auth import clients, ratelimit
from shared_kernel.errors.handlers import register_exception_handlers
from shared_kernel.storage import file_tickets
from shared_kernel.storage.minio_client import ObjectStat, ObjectStream
from tests.unit.file_ticket_support import FakeSigner

_T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
_BODY = b"x" * (3 * 1024 * 1024 + 17)


class _Minio:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {("exports", "job/export.md"): _BODY}
        self.closed = 0
        self.chunks_served = 0

    async def stat(self, *, bucket: str, key: str) -> ObjectStat | None:
        body = self.objects.get((bucket, key))
        return None if body is None else ObjectStat(size=len(body), etag="abc", content_type="x/stored")

    async def open_stream(self, *, bucket: str, key: str, chunk_size: int = 1 << 20) -> ObjectStream | None:
        body = self.objects.get((bucket, key))
        if body is None:
            return None
        resp = _Resp(body, self)
        return ObjectStream(resp, chunk_size)


class _Resp:
    """Stands in for the urllib3 response `Minio.get_object` returns."""

    def __init__(self, body: bytes, owner: _Minio | None = None) -> None:
        self._body = body
        self._pos = 0
        self._owner = owner
        self.headers = {"Content-Length": str(len(body)), "ETag": '"abc"', "Content-Type": "x/stored"}
        self.closed = False
        self.released = 0

    def read(self, amt: int) -> bytes:
        chunk = self._body[self._pos : self._pos + amt]
        self._pos += len(chunk)
        if chunk and self._owner is not None:
            self._owner.chunks_served += 1
        return chunk

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released += 1
        if self._owner is not None:
            self._owner.closed += 1


class _Limiter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.allow = True

    async def __call__(self, *, key: str, window_sec: int, max_count: int) -> ratelimit.Decision:
        self.calls.append(key)
        return ratelimit.Decision(
            allowed=self.allow,
            remaining=0,
            limit=max_count,
            reset_ms=0,
            retry_after_seconds=7,
        )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, _Minio, _Limiter]]:
    clock = {"now": _T0}
    clients.set_clock(lambda: clock["now"])
    monkeypatch.setattr(file_tickets, "_signer", FakeSigner)
    minio = _Minio()
    limiter = _Limiter()
    monkeypatch.setattr(files_route, "get_minio_client", lambda: minio)
    monkeypatch.setattr(files_route.ratelimit, "check_raw", limiter)
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(files_route.router)
    yield TestClient(app), minio, limiter
    clients.set_clock(lambda: datetime.now(UTC))


async def _path(key: str = "job/export.md", disposition: str = 'attachment; filename="room.md"') -> str:
    return await file_tickets.issue(
        bucket="exports", key=key, content_type="text/markdown", disposition=disposition
    )


async def test_streams_object_with_ticket_headers(env: tuple[TestClient, _Minio, _Limiter]) -> None:
    client, minio, _ = env
    r = client.get(await _path())
    assert r.status_code == 200
    assert r.content == _BODY
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"] == 'attachment; filename="room.md"'
    assert r.headers["content-length"] == str(len(_BODY))
    assert r.headers["etag"] == '"abc"'
    assert r.headers["cache-control"] == f"private, max-age={15 * 60}"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert minio.chunks_served == 4
    assert minio.closed == 1


async def test_file_csp_overrides_the_app_default(env: tuple[TestClient, _Minio, _Limiter]) -> None:
    client, _, _ = env
    csp = client.get(await _path()).headers["content-security-policy"]
    assert csp.startswith("sandbox")
    assert "default-src 'none'" in csp
    assert "script-src" not in csp


async def test_head_answers_from_stat_without_streaming(env: tuple[TestClient, _Minio, _Limiter]) -> None:
    client, minio, _ = env
    r = client.head(await _path())
    assert r.status_code == 200
    assert r.headers["content-length"] == str(len(_BODY))
    assert r.content == b""
    assert minio.chunks_served == 0


async def test_expired_ticket_is_410_not_401(env: tuple[TestClient, _Minio, _Limiter]) -> None:
    client, _, _ = env
    path = await _path()
    clients.set_clock(lambda: _T0 + timedelta(minutes=16))
    r = client.get(path)
    assert r.status_code == 410
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("/gone")


@pytest.mark.parametrize("ticket", ["garbage", "a.b", "e30.AAAA"])
def test_invalid_ticket_is_404(env: tuple[TestClient, _Minio, _Limiter], ticket: str) -> None:
    client, _, limiter = env
    assert client.get(f"/api/files/{ticket}").status_code == 404
    assert limiter.calls == []  # an unauthenticated ticket is never charged


async def test_missing_object_is_404(env: tuple[TestClient, _Minio, _Limiter]) -> None:
    client, _, _ = env
    assert client.get(await _path(key="job/gone.md")).status_code == 404


async def test_rate_limited_per_ticket(env: tuple[TestClient, _Minio, _Limiter]) -> None:
    client, _, limiter = env
    path = await _path()
    client.get(path)
    limiter.allow = False
    r = client.get(path)
    assert r.status_code == 429
    assert r.headers["retry-after"] == "7"
    assert len(set(limiter.calls)) == 1
    assert limiter.calls[0].startswith("rl:files:")
    assert path.removeprefix("/api/files/") not in limiter.calls[0]


def test_rate_limit_middleware_does_not_charge_file_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-IP OTHER bucket must not see `/api/files/` (spec Q-7)."""
    from app.api.middleware.rate_limit import RateLimitMiddleware

    charged: list[str] = []

    async def _check(
        *, bucket: ratelimit.Bucket, user_id: str | None, actor_ip: str | None
    ) -> ratelimit.Decision:
        charged.append(bucket.value)
        return ratelimit.Decision(allowed=True, remaining=1, limit=1, reset_ms=0, retry_after_seconds=0)

    monkeypatch.setattr(ratelimit, "check", _check)
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/api/files/{t}")
    def _file(t: str) -> dict[str, str]:
        return {}

    @app.get("/api/other")
    def _other() -> dict[str, str]:
        return {}

    client = TestClient(app)
    client.get("/api/files/abc")
    assert charged == []
    client.get("/api/other")
    assert charged == ["other"]


async def test_overlong_ticket_rejected(env: tuple[TestClient, _Minio, _Limiter]) -> None:
    client, _, _ = env
    assert client.get("/api/files/" + "a" * 5000).status_code == 404


async def test_stream_released_to_pool_after_full_read() -> None:
    resp = _Resp(b"x" * 10)
    stream = ObjectStream(resp, 4)
    assert b"".join([c async for c in stream.chunks()]) == b"x" * 10
    stream.release(aborted=True)  # the route's background task, after the fact
    assert resp.released == 1
    assert resp.closed is False  # a fully read connection is reusable


async def test_stream_abandoned_midway_closes_the_connection() -> None:
    resp = _Resp(b"x" * 10)
    stream = ObjectStream(resp, 4)
    chunks = stream.chunks()
    await anext(chunks)
    await chunks.aclose()
    assert resp.closed is True
    assert resp.released == 1


async def test_stream_never_started_is_released_by_the_caller() -> None:
    resp = _Resp(b"x" * 10)
    ObjectStream(resp, 4).release(aborted=True)
    assert resp.closed is True
    assert resp.released == 1
