"""`/api/projects/{pid}/mcp/egress-allowlist` — per-project egress allowlist (E.9).

MCP binding CRUD and test are on the unified ``/api/agents/{id}/tools``
surface in ``agents.py`` (Phase A). This module only handles the
project-scoped egress allowlist.
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.egress_allowlist_service import (
    EgressAllowlistService,
)
from contexts.agents.domain.mcp import EgressAllowlistEntry
from contexts.tenancy.domain.models import ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import ProjectMemberRepository
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    current_context,
    current_principal,
    require_membership,
)
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


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


__all__ = ["project_router"]
