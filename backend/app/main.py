"""FastAPI application factory.

Phase-C scope adds the identity / tenancy / web-security surface on top of
what Phase A scaffolded. Middleware order (earliest first) matters:

  0. CORSMiddleware           — opt-in; mounted only when `cors_origins` is
                                non-empty so preflight bypasses auth
  1. RequestIdMiddleware      — stamps `request_id` on every request
  2. TrustedProxyMiddleware   — resolves `actor_ip` under TRUSTED_PROXIES
  3. IpBanMiddleware          — short-circuits banned CIDRs with 403
  4. AuthMiddleware           — decodes JWT → Principal (non-fatal)
  5. RateLimitMiddleware      — uses Principal + actor_ip to pick buckets
  6. SecurityHeadersMiddleware— stamps §19a.2 headers on every response
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from app.api.middleware.auth import AuthMiddleware
from app.api.middleware.ip_ban import IpBanMiddleware
from app.api.middleware.rate_limit import RateLimitMiddleware
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.middleware.security_headers import SecurityHeadersMiddleware
from app.api.middleware.trusted_proxy import TrustedProxyMiddleware
from app.api.v1 import (
    admin as admin_routes,
    admin_ip_bans as admin_ip_ban_routes,
    agents as agent_routes,
    attachments as attachment_routes,
    auth as auth_routes,
    chatrooms as chatroom_routes,
    csp_report as csp_routes,
    exports as export_routes,
    guests as guest_routes,
    healthz,
    invites as invite_routes,
    key_groups as key_group_routes,
    keys as key_routes,
    mcp as mcp_routes,
    messages as message_routes,
    notifications as notification_routes,
    orchestration as orchestration_routes,
    project_keys as project_key_routes,
    graphrag as graphrag_routes,
    rag as rag_routes,
    search as search_routes,
    search_keys as search_key_routes,
    tus as tus_routes,
    metrics,
    orgs as org_routes,
    projects as project_routes,
    readyz,
    workflows as workflow_routes,
    workspaces as workspace_routes,
)
from app.api.ws import (
    admin_tail as ws_admin_tail,
    chatroom as ws_chatroom,
    rag_configs as ws_rag_configs,
    user as ws_user,
    workflow_runs as ws_workflow_runs,
)
from app.config.settings import get_settings
from contexts.agents.interfaces import error_mapping as agents_errors
from contexts.conversation.interfaces import error_mapping as conversation_errors
from contexts.identity.interfaces import error_mapping as identity_errors
from contexts.keys.interfaces import error_mapping as keys_errors
from contexts.knowledge.interfaces import error_mapping as knowledge_errors
from contexts.orchestration.interfaces import error_mapping as orchestration_errors
from contexts.tenancy.interfaces import error_mapping as tenancy_errors
from contexts.workflow.interfaces import error_mapping as workflow_errors
from shared_kernel.auth.clients import close_redis
from shared_kernel.db import registry as _db_registry  # noqa: F401 side-effect: table imports
from shared_kernel.db.session import dispose as dispose_db
from shared_kernel.errors.handlers import register_exception_handlers
from shared_kernel.logging.setup import configure_logging
from shared_kernel.observability.metrics import mount_metrics_middleware
from shared_kernel.observability.otel import install_otel


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings().logging)
    try:
        yield
    finally:
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

    # Starlette executes middleware in LIFO order (last added → first executed on
    # the way *in*). To match the numbered sequence in the module docstring above
    # (step 1 = RequestId runs first), add them here in reverse (step 6 → step 1).
    app.add_middleware(SecurityHeadersMiddleware)   # [6] last on request-in
    app.add_middleware(RateLimitMiddleware)         # [5]
    app.add_middleware(AuthMiddleware)              # [4]
    app.add_middleware(IpBanMiddleware)             # [3]
    app.add_middleware(TrustedProxyMiddleware)      # [2]
    app.add_middleware(RequestIdMiddleware)         # [1] first on request-in
    # CORS is opt-in: v1 deploys are same-origin behind nginx and ship no
    # CORS layer. If an operator splits origins, they configure
    # `SMAP_SEC_CORS_ORIGINS`; we then mount Starlette's CORSMiddleware
    # outside (i.e. before, since LIFO) the security stack so preflight
    # OPTIONS requests bypass auth + rate-limit. Empty list → no mount,
    # preserving the same-origin posture that the rest of the stack
    # assumes (see docs/operations.md §7).
    if settings.security.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.security.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
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
    # Per-context domain → HTTP mapping. Each context owns its own slugs.
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

    app.include_router(healthz.router)
    app.include_router(readyz.router)
    if settings.observability.metrics_enabled:
        app.include_router(metrics.router)
    app.include_router(auth_routes.router)
    app.include_router(org_routes.router)
    app.include_router(project_routes.router)
    app.include_router(invite_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(admin_ip_ban_routes.router)
    app.include_router(csp_routes.router)
    app.include_router(key_routes.router)
    app.include_router(project_key_routes.router)
    app.include_router(key_group_routes.project_router)
    app.include_router(key_group_routes.group_router)
    app.include_router(search_key_routes.router)
    app.include_router(agent_routes.project_router)
    app.include_router(agent_routes.agent_router)
    app.include_router(rag_routes.project_router)
    app.include_router(rag_routes.config_router)
    app.include_router(rag_routes.document_router)
    app.include_router(graphrag_routes.project_router)
    app.include_router(graphrag_routes.config_router)
    app.include_router(graphrag_routes.admin_router)
    app.include_router(mcp_routes.agent_router)
    app.include_router(mcp_routes.project_router)
    app.include_router(workspace_routes.project_router)
    app.include_router(workspace_routes.workspace_router)
    app.include_router(chatroom_routes.workspace_router)
    app.include_router(chatroom_routes.chatroom_router)
    app.include_router(message_routes.chatroom_router)
    app.include_router(message_routes.message_router)
    app.include_router(attachment_routes.chatroom_router)
    app.include_router(attachment_routes.attachment_router)
    app.include_router(tus_routes.router)
    app.include_router(guest_routes.router)
    app.include_router(search_routes.router)
    app.include_router(export_routes.chatroom_router)
    app.include_router(export_routes.export_router)
    app.include_router(workflow_routes.workspace_router)
    app.include_router(workflow_routes.workflow_router)
    app.include_router(workflow_routes.run_router)
    app.include_router(notification_routes.router)
    app.include_router(orchestration_routes.router)
    app.include_router(ws_user.router)
    app.include_router(ws_chatroom.router)
    app.include_router(ws_workflow_runs.router)
    app.include_router(ws_rag_configs.router)
    app.include_router(ws_admin_tail.router)

    return app


app = create_app()
