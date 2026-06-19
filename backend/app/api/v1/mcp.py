"""`/api/agents/{id}/mcp(/test)` + `/api/projects/{pid}/mcp/egress-allowlist`
— MCP binding test + per-project egress allowlist router (E.9).

The generic binding CRUD (list / add / delete) lives in
``app/api/v1/agents.py`` since E.1 already shipped it. This router only adds
the endpoints that are new in E.9:

- ``POST /api/agents/{id}/mcp/{mcp_id}/test`` — sandbox probe
- ``GET  /api/projects/{pid}/mcp/egress-allowlist``
- ``PUT  /api/projects/{pid}/mcp/egress-allowlist`` — replace full set
- ``POST /api/projects/{pid}/mcp/egress-allowlist`` — add one hostname
- ``DELETE /api/projects/{pid}/mcp/egress-allowlist/{hostname}``
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.agent_service import AgentService
from contexts.agents.application.egress_allowlist_service import (
    EgressAllowlistService,
)
from contexts.agents.application.mcp_service import McpBindingService
from contexts.agents.domain.mcp import EgressAllowlistEntry, McpTestResult
from contexts.tenancy.domain.models import ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import ProjectMemberRepository
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    current_context,
    current_principal,
    require_membership,
)
from shared_kernel.auth.permissions import Capability, Principal
from shared_kernel.db.session import db_session

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class McpTestOut(BaseModel):
    ok: bool
    tool_names: list[str]
    duration_ms: int
    error: str | None = None

    @classmethod
    def from_domain(cls, r: McpTestResult) -> McpTestOut:
        return cls(
            ok=r.ok,
            tool_names=list(r.tool_names),
            duration_ms=r.duration_ms,
            error=r.error,
        )


class AllowlistEntryOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    hostname: str
    added_by_user_id: uuid.UUID | None
    added_at: str
    note: str | None

    @classmethod
    def from_domain(cls, e: EgressAllowlistEntry) -> AllowlistEntryOut:
        return cls(
            id=e.id,
            project_id=e.project_id,
            hostname=e.hostname,
            added_by_user_id=e.added_by_user_id,
            added_at=e.added_at.isoformat(),
            note=e.note,
        )


# S9: RFC-1123 hostname pattern — no wildcards, whitespace, or non-DNS chars.
_HOSTNAME_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?" r"(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)


def _validate_hostname(v: str) -> str:
    if len(v) > 253:
        raise ValueError("hostname exceeds max DNS name length (253)")
    if not _HOSTNAME_RE.match(v):
        raise ValueError(
            "hostname contains invalid characters; "
            "only alphanumerics, hyphens, and dots are allowed (RFC 1123)"
        )
    return v


class AllowlistAddIn(BaseModel):
    hostname: str = Field(min_length=1, max_length=253)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("hostname")
    @classmethod
    def _check_hostname(cls, v: str) -> str:
        return _validate_hostname(v)


class AllowlistReplaceIn(BaseModel):
    hostnames: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("hostnames")
    @classmethod
    def _check_hostnames(cls, v: list[str]) -> list[str]:
        return [_validate_hostname(h) for h in v]


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def _require_owner(
    *,
    db: AsyncSession,
    project_id: uuid.UUID,
    principal: Principal,
) -> None:
    """R10.10-style guard — Project Owner (or Admin) only."""
    from shared_kernel.auth.dependencies import _raise_forbidden

    if principal.is_admin:
        return
    member = await ProjectMemberRepository(db).get(
        project_id=project_id,
        user_id=principal.user_id,
    )
    if member is None or member.role is not ProjectMemberRole.OWNER:
        _raise_forbidden(
            "Project Owner required to modify the MCP egress allowlist",
        )


def _get_sandbox_runner():  # pragma: no cover - runtime injection
    """Resolve the process-wide :class:`SandboxRunner`.

    Defaults to the settings-built :class:`DockerRunscSandbox` (digest-pinned
    images + pre-signed egress, K.5); tests override via
    ``app.dependency_overrides[_get_sandbox_runner] = ...``.
    """
    from contexts.agents.infrastructure.sandbox.docker_runsc import (
        docker_runsc_sandbox_from_settings,
    )

    return docker_runsc_sandbox_from_settings()


# ---------------------------------------------------------------------------
# Agent-scoped: MCP test
# ---------------------------------------------------------------------------

agent_router = APIRouter(prefix="/api/agents", tags=["mcp"])


@agent_router.post("/{agent_id}/mcp/{mcp_id}/test")
async def test_mcp_binding(
    agent_id: uuid.UUID = Path(...),
    mcp_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    runner=Depends(_get_sandbox_runner),
) -> McpTestOut:
    # Resolve the agent → project for the capability check.
    agent_service = AgentService(db)
    agent = await agent_service.get(agent_id)

    from shared_kernel.auth.dependencies import (
        _raise_forbidden,
        get_role_resolver,
    )
    from shared_kernel.auth.permissions import Scope, decide

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=agent.project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)

    service = McpBindingService(db, runner=runner)
    result = await service.test(
        agent_id=agent_id,
        binding_id=mcp_id,
        project_id=agent.project_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return McpTestOut.from_domain(result)


# ---------------------------------------------------------------------------
# Project-scoped: egress allowlist
# ---------------------------------------------------------------------------

project_router = APIRouter(
    prefix="/api/projects/{project_id}/mcp/egress-allowlist",
    tags=["mcp"],
)


@project_router.get("")
async def list_allowlist(
    project_id: uuid.UUID = Path(...),
    _=Depends(require_membership(project_param="project_id")),
    db: AsyncSession = Depends(db_session),
) -> list[AllowlistEntryOut]:
    service = EgressAllowlistService(db)
    rows = await service.list_for_project(project_id)
    return [AllowlistEntryOut.from_domain(r) for r in rows]


@project_router.put("")
async def replace_allowlist(
    body: AllowlistReplaceIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[AllowlistEntryOut]:
    await _require_owner(db=db, project_id=project_id, principal=principal)
    service = EgressAllowlistService(db)
    entries = await service.replace(
        project_id=project_id,
        hostnames=body.hostnames,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return [AllowlistEntryOut.from_domain(e) for e in entries]


@project_router.post("", status_code=status.HTTP_201_CREATED)
async def add_allowlist_entry(
    body: AllowlistAddIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AllowlistEntryOut:
    await _require_owner(db=db, project_id=project_id, principal=principal)
    service = EgressAllowlistService(db)
    entry = await service.add(
        project_id=project_id,
        hostname=body.hostname,
        note=body.note,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return AllowlistEntryOut.from_domain(entry)


@project_router.delete(
    "/{hostname}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def remove_allowlist_entry(
    project_id: uuid.UUID = Path(...),
    hostname: str = Path(..., min_length=1, max_length=253),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _require_owner(db=db, project_id=project_id, principal=principal)
    service = EgressAllowlistService(db)
    await service.remove(
        project_id=project_id,
        hostname=hostname,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


__all__ = ["agent_router", "project_router"]
