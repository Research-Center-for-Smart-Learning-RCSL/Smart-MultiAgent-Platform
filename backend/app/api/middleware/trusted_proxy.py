"""Resolve `actor_ip` honouring `TRUSTED_PROXIES` (R19a.10, R19a.11).

Mounted very early so every downstream middleware (ip-ban, rate-limit, auth)
sees the trusted value on `request.state.auth_ctx.actor_ip`.
"""

from __future__ import annotations

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config.settings import get_settings
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.trusted_proxy import resolve_actor_ip
from shared_kernel.errors.problem import Problem, problem_type


class TrustedProxyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        ctx = getattr(request.state, "auth_ctx", None) or RequestContext()
        if request.client is None:
            # No peer IP means downstream IP-ban + rate-limit middlewares would
            # silently key on a default value, defeating both. Reject the
            # request rather than fail open.
            logger.bind(
                event="trusted_proxy_no_client",
                path=request.url.path,
            ).warning("rejecting request: no peer IP available")
            problem = Problem(
                type=problem_type("auth/no-client-ip"),
                title="Cannot determine client IP",
                status=400,
                detail="request has no peer address",
            )
            body = problem.dump()
            body["instance"] = str(request.url.path)
            return JSONResponse(
                status_code=400, content=body,
                media_type="application/problem+json",
            )
        peer = request.client.host
        xff = request.headers.get("X-Forwarded-For")
        ctx.actor_ip = resolve_actor_ip(
            peer_ip=peer,
            forwarded_for=xff,
            trusted_cidrs=get_settings().security.trusted_proxies,
        )
        request.state.auth_ctx = ctx
        return await call_next(request)
