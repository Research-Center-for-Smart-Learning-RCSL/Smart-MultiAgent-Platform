"""Resolve `actor_ip` honouring `TRUSTED_PROXIES` (R19a.10, R19a.11).

Mounted very early so every downstream middleware (ip-ban, rate-limit, auth)
sees the trusted value on `request.state.auth_ctx.actor_ip`.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config.settings import get_settings
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.trusted_proxy import resolve_actor_ip


class TrustedProxyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        ctx = getattr(request.state, "auth_ctx", None) or RequestContext()
        peer = request.client.host if request.client else "127.0.0.1"
        xff = request.headers.get("X-Forwarded-For")
        ctx.actor_ip = resolve_actor_ip(
            peer_ip=peer,
            forwarded_for=xff,
            trusted_cidrs=get_settings().security.trusted_proxies,
        )
        request.state.auth_ctx = ctx
        return await call_next(request)
