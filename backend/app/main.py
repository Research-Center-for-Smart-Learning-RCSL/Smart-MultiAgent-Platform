"""FastAPI application factory.

Phase-C scope adds the identity / tenancy / web-security surface on top of
what Phase A scaffolded. Middleware order (earliest first) matters:

  0. CORSMiddleware                -- opt-in; mounted only when `cors_origins`
                                     is non-empty so preflight bypasses auth
  1. RequestIdMiddleware           -- stamps `request_id` on every request
  2. TrustedProxyMiddleware        -- resolves `actor_ip` under TRUSTED_PROXIES
  3. IpBanMiddleware               -- short-circuits banned CIDRs with 403
  4. AuthMiddleware                -- decodes JWT -> Principal (non-fatal)
  5. ImpersonationPolicyMiddleware -- enforces read-only + download-deny
  6. RateLimitMiddleware           -- uses Principal + actor_ip to pick buckets
  7. SecurityHeadersMiddleware     -- stamps 19a.2 headers on every response
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from app.api.middleware.auth import AuthMiddleware
from app.api.middleware.impersonation import ImpersonationPolicyMiddleware
from app.api.middleware.ip_ban import IpBanMiddleware
from app.api.middleware.rate_limit import RateLimitMiddleware
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.middleware.security_headers import SecurityHeadersMiddleware
from app.api.middleware.trusted_proxy import TrustedProxyMiddleware
from app.api.v1 import get_router_registry
from app.bootstrap.startup import INITIALIZERS
from app.config.settings import get_settings
from contexts.agents.interfaces import error_mapping as agents_errors
from contexts.conversation.interfaces import error_mapping as conversation_errors
from contexts.identity.interfaces import error_mapping as identity_errors
from contexts.keys.infrastructure import revocation_listener
from contexts.keys.interfaces import error_mapping as keys_errors
from contexts.knowledge.interfaces import error_mapping as knowledge_errors
from contexts.orchestration.interfaces import error_mapping as orchestration_errors
from contexts.tenancy.interfaces import error_mapping as tenancy_errors
from contexts.workflow.interfaces import error_mapping as workflow_errors
from shared_kernel.auth.clients import close_redis
import app.db_registry as _db_registry  # noqa: F401 -- side-effect: table imports
from shared_kernel.db.session import dispose as dispose_db
from shared_kernel.errors.handlers import register_exception_handlers
from shared_kernel.observability.metrics import mount_metrics_middleware
from shared_kernel.observability.otel import install_otel


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    # Run each startup initializer in sequence.
    for initializer in INITIALIZERS:
        await initializer(settings)

    # ASYNC-2: subscribe this process to key-revocation events so a revoked or
    # carry-withdrawn DEK is punched out of the in-process provider_router cache.
    # Without this listener a cached DEK keeps working until its TTL expires.
    revocation_task = asyncio.create_task(
        revocation_listener.run(),
        name="key-revocation-listener",
    )
    try:
        yield
    finally:
        # Cancel the listener before closing Redis -- its cancellation path
        # unsubscribes and closes the pub/sub channel, which needs a live client.
        revocation_task.cancel()
        with suppress(asyncio.CancelledError):
            await revocation_task
        await close_redis()
        await dispose_db()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="SMAP",
        version=settings.app.version,
        default_response_class=ORJSONResponse,
        lifespan=_lifespan,
        docs_url="/api/docs" if settings.app.docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.app.docs_enabled else None,
    )

    # Starlette executes middleware in LIFO order (last added -> first executed on
    # the way *in*). To match the numbered sequence in the module docstring above
    # (step 1 = RequestId runs first), add them here in reverse (step 6 -> step 1).
    app.add_middleware(SecurityHeadersMiddleware)  # [7] last on request-in
    app.add_middleware(RateLimitMiddleware)  # [6]
    app.add_middleware(ImpersonationPolicyMiddleware)  # [5] impersonation policy
    app.add_middleware(AuthMiddleware)  # [4] JWT verify + Principal
    app.add_middleware(IpBanMiddleware)  # [3]
    app.add_middleware(TrustedProxyMiddleware)  # [2]
    app.add_middleware(RequestIdMiddleware)  # [1] first on request-in
    # CORS is opt-in: v1 deploys are same-origin behind nginx and ship no
    # CORS layer. If an operator splits origins, they configure
    # `SMAP_SEC_CORS_ORIGINS`; we then mount Starlette's CORSMiddleware
    # outside (i.e. before, since LIFO) the security stack so preflight
    # OPTIONS requests bypass auth + rate-limit. Empty list -> no mount,
    # preserving the same-origin posture that the rest of the stack
    # assumes (see docs/operations.md 7).
    if settings.security.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.security.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            # API-8: explicit request-header allowlist. `["*"]` is invalid to
            # pair with credentialed requests (the browser ignores the wildcard
            # and the SPA uses cookie auth), so enumerate exactly what the SPA
            # sends: bearer auth, JSON bodies, optimistic-concurrency If-Match,
            # the TUS resumable-upload headers, and the request-id correlator.
            allow_headers=[
                "Authorization",
                "Content-Type",
                "If-Match",
                "Tus-Resumable",
                "Upload-Length",
                "Upload-Metadata",
                "Upload-Offset",
                "X-Request-ID",
            ],
            expose_headers=[
                "X-Request-ID",
                "X-RateLimit-Limit",
                "X-RateLimit-Remaining",
                "X-RateLimit-Reset",
                "Retry-After",
            ],
            max_age=600,
        )

    register_exception_handlers(app)
    # Per-context domain -> HTTP mapping. Each context owns its own slugs.
    identity_errors.register(app)
    tenancy_errors.register(app)
    keys_errors.register(app)
    agents_errors.register(app)
    knowledge_errors.register(app)
    conversation_errors.register(app)
    orchestration_errors.register(app)
    workflow_errors.register(app)
    mount_metrics_middleware(app, settings.observability)
    install_otel(app, settings.observability)

    # Mount all v1 routers from the registry.
    for entry in get_router_registry():
        if entry.condition is not None and not entry.condition():
            continue
        app.include_router(entry.router)

    return app


app = create_app()
