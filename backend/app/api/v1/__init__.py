"""v1 API router registry.

Collects all routers into a single list so the application factory can
mount them with a loop instead of 49 individual ``include_router`` calls.
Conditional routers (e.g. metrics) are handled by the ``conditional``
flag -- the factory checks it at mount time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class RouterEntry:
    """A router plus an optional guard that decides whether to mount it."""

    router: APIRouter
    # When set, the router is only mounted if the callable returns True.
    condition: Callable[[], bool] | None = None


def _build_registry() -> list[RouterEntry]:
    """Build the full list of v1 routers.

    Imports are done inside this function to avoid circular import issues
    at module load time and to keep the registry self-contained.
    """
    from app.api.v1 import (
        admin as admin_routes,
        admin_ip_bans as admin_ip_ban_routes,
        agents as agent_routes,
        attachments as attachment_routes,
        auth as auth_routes,
        chatrooms as chatroom_routes,
        csp_report as csp_routes,
        exports as export_routes,
        graphrag as graphrag_routes,
        guests as guest_routes,
        healthz,
        invites as invite_routes,
        key_groups as key_group_routes,
        keys as key_routes,
        mcp as mcp_routes,
        messages as message_routes,
        metrics,
        notifications as notification_routes,
        orchestration as orchestration_routes,
        orgs as org_routes,
        project_keys as project_key_routes,
        projects as project_routes,
        rag as rag_routes,
        readyz,
        search as search_routes,
        search_keys as search_key_routes,
        tus as tus_routes,
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

    def _metrics_enabled() -> bool:
        return get_settings().observability.metrics_enabled

    return [
        # Infrastructure
        RouterEntry(healthz.router),
        RouterEntry(readyz.router),
        RouterEntry(metrics.router, condition=_metrics_enabled),
        # Auth
        RouterEntry(auth_routes.router),
        # Tenancy
        RouterEntry(org_routes.router),
        RouterEntry(project_routes.router),
        RouterEntry(invite_routes.router),
        # Admin
        RouterEntry(admin_routes.router),
        RouterEntry(admin_ip_ban_routes.router),
        RouterEntry(csp_routes.router),
        # Keys
        RouterEntry(key_routes.router),
        RouterEntry(project_key_routes.router),
        RouterEntry(key_group_routes.project_router),
        RouterEntry(key_group_routes.group_router),
        RouterEntry(search_key_routes.router),
        # Agents
        RouterEntry(agent_routes.project_router),
        RouterEntry(agent_routes.agent_router),
        # RAG
        RouterEntry(rag_routes.project_router),
        RouterEntry(rag_routes.config_router),
        RouterEntry(rag_routes.document_router),
        # GraphRAG
        RouterEntry(graphrag_routes.project_router),
        RouterEntry(graphrag_routes.config_router),
        RouterEntry(graphrag_routes.admin_router),
        # MCP
        RouterEntry(mcp_routes.agent_router),
        RouterEntry(mcp_routes.project_router),
        # Conversation
        RouterEntry(workspace_routes.project_router),
        RouterEntry(workspace_routes.workspace_router),
        RouterEntry(chatroom_routes.workspace_router),
        RouterEntry(chatroom_routes.chatroom_router),
        RouterEntry(message_routes.chatroom_router),
        RouterEntry(message_routes.message_router),
        RouterEntry(attachment_routes.chatroom_router),
        RouterEntry(attachment_routes.attachment_router),
        RouterEntry(tus_routes.router),
        RouterEntry(guest_routes.router),
        RouterEntry(search_routes.router),
        RouterEntry(export_routes.chatroom_router),
        RouterEntry(export_routes.export_router),
        # Workflow
        RouterEntry(workflow_routes.workspace_router),
        RouterEntry(workflow_routes.workflow_router),
        RouterEntry(workflow_routes.run_router),
        # Notifications
        RouterEntry(notification_routes.router),
        # Orchestration
        RouterEntry(orchestration_routes.router),
        # WebSockets
        RouterEntry(ws_user.router),
        RouterEntry(ws_chatroom.router),
        RouterEntry(ws_workflow_runs.router),
        RouterEntry(ws_rag_configs.router),
        RouterEntry(ws_admin_tail.router),
    ]


def get_router_registry() -> list[RouterEntry]:
    """Return the full router registry (built lazily on first call)."""
    return _build_registry()


__all__ = ["RouterEntry", "get_router_registry"]
