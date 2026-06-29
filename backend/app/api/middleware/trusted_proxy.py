"""Resolve `actor_ip` honouring `TRUSTED_PROXIES` (R19a.10, R19a.11).

Mounted very early so every downstream middleware (ip-ban, rate-limit, auth)
sees the trusted value on `request.state.auth_ctx.actor_ip`.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config.settings import get_settings
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.trusted_proxy import resolve_actor_ip
from shared_kernel.errors.problem import Problem, problem_type
from shared_kernel.observability.metrics import TRUSTED_PROXY_UNTRUSTED_PEER

# Warn at most once per process: a persistent misconfiguration would otherwise
# log on every request. This is a config smell, not a per-request event.
_warned_untrusted_peer = False


def _peer_trusted(peer: str, trusted_cidrs: Sequence[str]) -> bool:
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for cidr in trusted_cidrs:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


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
                status_code=400,
                content=body,
                media_type="application/problem+json",
            )
        peer = request.client.host
        xff = request.headers.get("X-Forwarded-For")
        trusted = get_settings().security.trusted_proxies
        ctx.actor_ip = resolve_actor_ip(
            peer_ip=peer,
            forwarded_for=xff,
            trusted_cidrs=trusted,
        )
        # SEC-M1 config smell: a forwarded header arriving from a peer that is
        # NOT in the trust list means a reverse proxy sits in front but its
        # subnet was not configured — so X-Forwarded-For is being discarded and
        # every client collapses to this peer IP, defeating per-IP controls.
        if xff and not _peer_trusted(peer, trusted):
            # Increment every occurrence so `rate()` can drive an alert; the log
            # below is throttled to once per process to avoid per-request spam.
            TRUSTED_PROXY_UNTRUSTED_PEER.inc()
            global _warned_untrusted_peer
            if not _warned_untrusted_peer:
                _warned_untrusted_peer = True
                logger.bind(event="trusted_proxy_peer_untrusted", peer=peer).warning(
                    "X-Forwarded-For received from untrusted peer {peer}; the header "
                    "is being ignored and all clients collapse to this IP. Add the "
                    "reverse-proxy subnet (e.g. the Docker bridge 172.16.0.0/12) to "
                    "SMAP_SEC_TRUSTED_PROXIES.",
                    peer=peer,
                )
        request.state.auth_ctx = ctx
        return await call_next(request)
