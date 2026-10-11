"""`GET|HEAD /api/files/{ticket}` — stream a stored object to the browser ([R19a.14]).

Public by omission of a principal dependency: the ticket is the credential,
because `<img>`, anchors and `window.open` cannot send a Bearer header. No
failure answers 401, since the SPA's transport treats 401 as a dead session
and would try to refresh or sign the user out over a stale link.

Design: docs/tasks/2026-10-11-browser-file-delivery/spec.md §7.3-§7.4.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from app.config.settings import get_settings
from shared_kernel.auth import ratelimit
from shared_kernel.auth.clients import now
from shared_kernel.errors.handlers import problem_response
from shared_kernel.errors.problem import Problem, problem_type
from shared_kernel.storage import file_tickets
from shared_kernel.storage.file_tickets import FileTicket, TicketExpired, TicketInvalid
from shared_kernel.storage.minio_client import ObjectStat, ObjectStream, get_minio_client

router = APIRouter(prefix="/api/files", tags=["files"])

# Files come from the app origin, so a hostile document must not gain its
# script or origin privileges: `sandbox` strips both. Images still render.
_FILE_CSP = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
_MAX_TICKET_CHARS = 4096
_RATE_WINDOW_SECONDS = 60
# Longest a stream may hold a concurrency slot before it is presumed leaked;
# 1 GiB at ~1 MB/s fits.
_STREAM_HOLD_SECONDS = 30 * 60
_STREAM_RETRY_SECONDS = 30
# One range only; a multi-range request is answered with the whole object.
_RANGE = re.compile(r"bytes=(\d{0,19})-(\d{0,19})")


async def _authorise(ticket: str) -> FileTicket:
    if len(ticket) > _MAX_TICKET_CHARS:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        verified = await file_tickets.verify(ticket)
    except TicketInvalid as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    except TicketExpired as exc:
        raise HTTPException(status_code=410, detail="Download link expired") from exc
    decision = await ratelimit.check_raw(
        key=f"rl:files:t:{verified.rate_key}",
        window_sec=_RATE_WINDOW_SECONDS,
        max_count=get_settings().limits.files_per_min_ticket,
    )
    if not decision.allowed:
        # Not HTTPException: the shared problem handler drops its headers.
        raise _RateLimited(decision.retry_after_seconds)
    return verified


class _RateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after


def _rate_limited_response(request: Request, retry_after: int) -> Response:
    problem = Problem(
        type=problem_type("rate-limited"),
        title="Too Many Requests",
        status=429,
        detail="Too many downloads of this link",
    )
    return problem_response(problem, instance=request.url.path, headers={"Retry-After": str(retry_after)})


def _headers(ticket: FileTicket, stat: ObjectStat, *, length: int | None = None) -> dict[str, str]:
    remaining = max(0, int((ticket.expires_at - now()).total_seconds()))
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": ticket.disposition,
        "Content-Length": str(stat.size if length is None else length),
        # A download (export, audit CSV) must not linger in a shared machine's
        # disk cache; inline images may, for as long as the link lives.
        "Cache-Control": "no-store"
        if ticket.disposition.startswith("attachment")
        else f"private, max-age={remaining}",
        "Content-Security-Policy": _FILE_CSP,
        "X-Content-Type-Options": "nosniff",
    }
    if stat.etag:
        headers["ETag"] = f'"{stat.etag}"'
    return headers


async def _verified_or_429(request: Request, ticket: str) -> FileTicket | Response:
    try:
        return await _authorise(ticket)
    except _RateLimited as exc:
        return _rate_limited_response(request, exc.retry_after)


class _Unsatisfiable(Exception):
    pass


def _byte_range(request: Request, stat: ObjectStat) -> tuple[int, int] | None:
    """The single inclusive byte range to serve, or ``None`` for the whole object.

    Per RFC 9110 §14, a header this does not understand (multiple ranges, bad
    syntax) is ignored rather than refused, and a stale `If-Range` validator
    means the client gets the full current object.
    """
    match = _RANGE.fullmatch(request.headers.get("range", "").strip())
    if match is None:
        return None
    if_range = request.headers.get("if-range")
    if if_range is not None and (not stat.etag or if_range.strip() != f'"{stat.etag}"'):
        return None
    first, last = match.groups()
    if first:
        start = int(first)
        end = min(int(last), stat.size - 1) if last else stat.size - 1
        if last and int(last) < start:
            return None
        if start >= stat.size:
            raise _Unsatisfiable
        return start, end
    if not last:
        return None
    suffix = int(last)
    if suffix == 0 or stat.size == 0:
        raise _Unsatisfiable
    return max(0, stat.size - suffix), stat.size - 1


class _ObjectResponse(StreamingResponse):
    """Streams an object and frees its connection and concurrency slot however
    the response ends.

    A `background` task cannot do this: Starlette skips it when the client
    disconnects or an enclosing BaseHTTPMiddleware cancels the app, which would
    hold the slot for the full hold window. A release that still never runs
    (the response is never called) is reclaimed by that window.
    """

    def __init__(self, stream: ObjectStream, slot_key: str, slot: str, **kwargs: Any) -> None:
        super().__init__(stream.chunks(), **kwargs)
        self._stream = stream
        self._slot_key = slot_key
        self._slot = slot

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._stream.release(aborted=True)
            # release_slot counts its own Redis failures; the hold window
            # reclaims the slot, so a failure must not mask the response's.
            with anyio.CancelScope(shield=True), contextlib.suppress(Exception):
                await ratelimit.release_slot(key=self._slot_key, token=self._slot)


@router.get("/{ticket}", response_class=Response)
async def get_file(request: Request, ticket: str = Path(...)) -> Response:
    verified = await _verified_or_429(request, ticket)
    if isinstance(verified, Response):
        return verified
    minio = get_minio_client()
    byte_range: tuple[int, int] | None = None
    total: ObjectStat | None = None
    if "range" in request.headers:
        total = await minio.stat(bucket=verified.bucket, key=verified.key)
        if total is None:
            raise HTTPException(status_code=404, detail="File not found")
        try:
            byte_range = _byte_range(request, total)
        except _Unsatisfiable:
            return problem_response(
                Problem(
                    type=problem_type("range-not-satisfiable"),
                    title="Range Not Satisfiable",
                    status=416,
                ),
                instance=request.url.path,
                headers={"Content-Range": f"bytes */{total.size}"},
            )
    slot_key = f"files:streams:{verified.rate_key}"
    slot = await ratelimit.acquire_slot(
        key=slot_key,
        max_count=get_settings().limits.files_concurrent_per_ticket,
        hold_sec=_STREAM_HOLD_SECONDS,
    )
    if slot is None:
        return _rate_limited_response(request, _STREAM_RETRY_SECONDS)
    offset, length = (byte_range[0], byte_range[1] - byte_range[0] + 1) if byte_range else (0, 0)
    try:
        stream = await minio.open_stream(
            bucket=verified.bucket, key=verified.key, offset=offset, length=length
        )
    except BaseException:
        await ratelimit.release_slot(key=slot_key, token=slot)
        raise
    if stream is None:
        await ratelimit.release_slot(key=slot_key, token=slot)
        raise HTTPException(status_code=404, detail="File not found")
    if byte_range is None or total is None:
        return _ObjectResponse(
            stream, slot_key, slot, media_type=verified.content_type, headers=_headers(verified, stream.stat)
        )
    headers = _headers(verified, total, length=length)
    headers["Content-Range"] = f"bytes {byte_range[0]}-{byte_range[1]}/{total.size}"
    return _ObjectResponse(
        stream, slot_key, slot, status_code=206, media_type=verified.content_type, headers=headers
    )


@router.head("/{ticket}", response_class=Response)
async def head_file(request: Request, ticket: str = Path(...)) -> Response:
    verified = await _verified_or_429(request, ticket)
    if isinstance(verified, Response):
        return verified
    stat = await get_minio_client().stat(bucket=verified.bucket, key=verified.key)
    if stat is None:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(status_code=200, media_type=verified.content_type, headers=_headers(verified, stat))


__all__ = ["router"]
