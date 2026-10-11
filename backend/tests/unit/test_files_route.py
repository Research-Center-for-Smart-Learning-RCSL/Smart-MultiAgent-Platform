"""`GET|HEAD /api/files/{ticket}` streams one stored object ([R19a.14]).

Dossier: docs/tasks/2026-10-11-browser-file-delivery/spec.md §7.3, §7.4, §8.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect
from starlette.types import Message

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

    async def open_stream(
        self, *, bucket: str, key: str, offset: int = 0, length: int = 0, chunk_size: int = 1 << 20
    ) -> ObjectStream | None:
        body = self.objects.get((bucket, key))
        if body is None:
            return None
        resp = _Resp(body[offset : offset + length] if length else body[offset:], self)
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
        self.slots: dict[str, set[str]] = {}
        self.max_slots = 2

    async def acquire_slot(self, *, key: str, max_count: int, hold_sec: int) -> str | None:
        held = self.slots.setdefault(key, set())
        if len(held) >= min(max_count, self.max_slots):
            return None
        token = f"t{len(held)}-{key}"
        held.add(token)
        return token

    async def release_slot(self, *, key: str, token: str) -> None:
        self.slots.get(key, set()).discard(token)

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
    monkeypatch.setattr(files_route.ratelimit, "acquire_slot", limiter.acquire_slot)
    monkeypatch.setattr(files_route.ratelimit, "release_slot", limiter.release_slot)
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
    assert r.headers["cache-control"] == "no-store"  # an attachment download
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
    stream.release(aborted=True)  # the response's own cleanup, after the fact
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


async def test_attachment_downloads_are_not_cached_but_inline_images_are(
    env: tuple[TestClient, _Minio, _Limiter],
) -> None:
    client, minio, _ = env
    assert client.get(await _path()).headers["cache-control"] == "no-store"
    minio.objects[("exports", "img.png")] = b"png"
    inline = await file_tickets.issue(
        bucket="exports", key="img.png", content_type="image/png", disposition='inline; filename="img.png"'
    )
    assert client.get(inline).headers["cache-control"].startswith("private, max-age=")


async def test_concurrent_streams_per_ticket_are_capped(env: tuple[TestClient, _Minio, _Limiter]) -> None:
    """A leaked ticket cannot fan out into unbounded parallel streams (FU-5)."""
    client, _, limiter = env
    path = await _path()
    key = next(iter(limiter.slots), None)
    assert key is None
    # Two streams held open elsewhere fill the ticket's slots.
    limiter.max_slots = 0
    r = client.get(path)
    assert r.status_code == 429
    assert r.headers["retry-after"]


async def test_stream_slot_is_released_when_the_response_ends(
    env: tuple[TestClient, _Minio, _Limiter],
) -> None:
    client, _, limiter = env
    path = await _path()
    for _ in range(3):  # more sequential downloads than slots: each one frees its slot
        assert client.get(path).status_code == 200
    assert all(not held for held in limiter.slots.values())


async def test_head_takes_no_stream_slot(env: tuple[TestClient, _Minio, _Limiter]) -> None:
    client, _, limiter = env
    limiter.max_slots = 0
    assert client.head(await _path()).status_code == 200


def _disconnecting_send() -> Callable[[Message], Awaitable[None]]:
    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            raise OSError("peer went away")

    return send


async def _no_receive() -> Message:
    raise AssertionError("not consulted on ASGI spec 2.4")


async def test_slot_and_connection_are_released_when_the_client_disconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starlette skips `background` on a disconnect; a held slot would make the
    ticket answer 429 for the rest of its life (FU-6)."""
    released: list[str] = []

    async def _release(*, key: str, token: str) -> None:
        released.append(token)

    monkeypatch.setattr(files_route.ratelimit, "release_slot", _release)
    resp = _Resp(b"x" * 10)
    response = files_route._ObjectResponse(ObjectStream(resp, 4), "k", "tok", media_type="x/y")
    with pytest.raises(ClientDisconnect):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, _no_receive, _disconnecting_send())
    assert released == ["tok"]
    assert resp.closed is True
    assert resp.released == 1


async def test_slot_is_released_when_the_app_is_cancelled_mid_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An enclosing BaseHTTPMiddleware cancels the app on disconnect."""
    released: list[str] = []

    async def _release(*, key: str, token: str) -> None:
        await asyncio.sleep(0)  # must survive the cancellation it runs under
        released.append(token)

    monkeypatch.setattr(files_route.ratelimit, "release_slot", _release)
    blocked = asyncio.Event()

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            blocked.set()
            await asyncio.Event().wait()

    response = files_route._ObjectResponse(ObjectStream(_Resp(b"x" * 10), 4), "k", "tok", media_type="x/y")
    task = asyncio.create_task(response({"type": "http", "asgi": {"spec_version": "2.4"}}, _no_receive, send))
    await blocked.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released == ["tok"]


async def test_release_during_an_in_flight_read_defers_the_close_to_the_reader() -> None:
    """urllib3 responses are not thread-safe: `release` must not close the
    response while a cancelled `to_thread` read is still running on it."""
    gate = threading.Event()
    reading = threading.Event()

    class _SlowResp(_Resp):
        def read(self, amt: int) -> bytes:
            reading.set()
            gate.wait(5)
            assert not self.closed, "closed underneath a running read"
            return super().read(amt)

    resp = _SlowResp(b"x" * 10)
    stream = ObjectStream(resp, 4)
    pending: asyncio.Task[bytes] = asyncio.create_task(anext(stream.chunks()))
    await asyncio.to_thread(reading.wait, 5)
    stream.release(aborted=True)
    assert resp.closed is False
    gate.set()
    with pytest.raises(StopAsyncIteration):
        await pending
    assert resp.closed is True
    assert resp.released == 1


@pytest.mark.parametrize(
    ("header", "start", "end"),
    [
        ("bytes=0-9", 0, 9),
        ("bytes=3145728-", 3145728, len(_BODY) - 1),
        ("bytes=-17", len(_BODY) - 17, len(_BODY) - 1),
        ("bytes=10-999999999", 10, len(_BODY) - 1),
    ],
)
async def test_range_request_resumes_a_download(
    env: tuple[TestClient, _Minio, _Limiter], header: str, start: int, end: int
) -> None:
    client, _, limiter = env
    r = client.get(await _path(), headers={"Range": header})
    assert r.status_code == 206
    assert r.content == _BODY[start : end + 1]
    assert r.headers["content-range"] == f"bytes {start}-{end}/{len(_BODY)}"
    assert r.headers["content-length"] == str(end - start + 1)
    assert all(not held for held in limiter.slots.values())


async def test_full_response_advertises_ranges(env: tuple[TestClient, _Minio, _Limiter]) -> None:
    client, _, _ = env
    assert client.get(await _path()).headers["accept-ranges"] == "bytes"
    assert client.head(await _path()).headers["accept-ranges"] == "bytes"


async def test_range_past_the_end_is_416(env: tuple[TestClient, _Minio, _Limiter]) -> None:
    client, minio, _ = env
    r = client.get(await _path(), headers={"Range": f"bytes={len(_BODY)}-"})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{len(_BODY)}"
    assert minio.chunks_served == 0


@pytest.mark.parametrize(
    "headers",
    [
        {"Range": "bytes=0-9", "If-Range": '"changed"'},
        {"Range": "bytes=0-9,20-29"},
        {"Range": "bytes=9-3"},
        {"Range": "items=0-9"},
    ],
)
async def test_unusable_range_serves_the_whole_object(
    env: tuple[TestClient, _Minio, _Limiter], headers: dict[str, str]
) -> None:
    client, _, _ = env
    r = client.get(await _path(), headers=headers)
    assert r.status_code == 200
    assert r.content == _BODY


async def test_matching_if_range_honours_the_range(env: tuple[TestClient, _Minio, _Limiter]) -> None:
    client, _, _ = env
    r = client.get(await _path(), headers={"Range": "bytes=0-9", "If-Range": '"abc"'})
    assert r.status_code == 206
