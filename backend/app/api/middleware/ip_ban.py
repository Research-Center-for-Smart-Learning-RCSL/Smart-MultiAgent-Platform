"""Earliest-layer IP-ban short-circuit (R19.05, R6.13)."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from shared_kernel.auth import ip_bans
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.errors.problem import Problem, problem_type


class IpBanMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        # Must run AFTER TrustedProxyMiddleware — it relies on `actor_ip`.
        ctx = getattr(request.state, "auth_ctx", None)
        ip = ctx.actor_ip if ctx else (request.client.host if request.client else None)
        if ip is None:
            return await call_next(request)

        # Fast path: cache is fresh → no DB round-trip. Only reload when stale
        # (once every 5 s per worker) so the earliest-layer check remains cheap.
        if not ip_bans.cache_is_fresh():
            sm = get_sessionmaker()
            async with sm() as session:
                await ip_bans.reload(session)

        if ip_bans.is_banned_cached(ip):
            problem = Problem(
                type=problem_type("ip-banned"),
                title="Forbidden",
                status=403,
                detail="Your IP is banned from this service.",
            )
            body = problem.dump()
            body["instance"] = str(request.url.path)
            return JSONResponse(
                status_code=403,
                content=body,
                media_type="application/problem+json",
            )
        return await call_next(request)
