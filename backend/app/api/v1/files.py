"""`GET|HEAD /api/files/{ticket}` — stream a stored object to the browser ([R19a.14]).

Public by omission of a principal dependency: the ticket is the credential,
because `<img>`, anchors and `window.open` cannot send a Bearer header. No
failure answers 401, since the SPA's transport treats 401 as a dead session
and would try to refresh or sign the user out over a stale link.

Design: docs/tasks/2026-10-11-browser-file-delivery/spec.md §7.3-§7.4.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from app.config.settings import get_settings
from shared_kernel.auth import ratelimit
from shared_kernel.auth.clients import now
from shared_kernel.errors.handlers import problem_response
from shared_kernel.errors.problem import Problem, problem_type
from shared_kernel.storage import file_tickets
from shared_kernel.storage.file_tickets import FileTicket, TicketExpired, TicketInvalid
from shared_kernel.storage.minio_client import ObjectStat, get_minio_client

router = APIRouter(prefix="/api/files", tags=["files"])

# Files come from the app origin, so a hostile document must not gain its
# script or origin privileges: `sandbox` strips both. Images still render.
_FILE_CSP = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
_MAX_TICKET_CHARS = 4096
_RATE_WINDOW_SECONDS = 60


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


def _headers(ticket: FileTicket, stat: ObjectStat) -> dict[str, str]:
    remaining = max(0, int((ticket.expires_at - now()).total_seconds()))
    headers = {
        "Content-Disposition": ticket.disposition,
        "Content-Length": str(stat.size),
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


@router.get("/{ticket}", response_class=Response)
async def get_file(request: Request, ticket: str = Path(...)) -> Response:
    verified = await _verified_or_429(request, ticket)
    if isinstance(verified, Response):
        return verified
    stream = await get_minio_client().open_stream(bucket=verified.bucket, key=verified.key)
    if stream is None:
        raise HTTPException(status_code=404, detail="File not found")
    return StreamingResponse(
        stream.chunks(),
        media_type=verified.content_type,
        headers=_headers(verified, stream.stat),
        # Runs however the response ends; the iterator alone is not closed on a
        # disconnect or when it never starts.
        background=BackgroundTask(stream.release, aborted=True),
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
