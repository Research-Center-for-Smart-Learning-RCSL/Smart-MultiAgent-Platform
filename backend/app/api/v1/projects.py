"""`/api/projects/*` — project CRUD + membership (§22.3)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import Response, APIRouter, Depends, Header, HTTPException, Path, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.tenancy.application.invite_service import InviteService
from contexts.tenancy.application.project_service import (
    ProjectMemberRole,
    ProjectOwnerType,
    ProjectService,
)
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    current_context,
    current_principal,
    require,
    require_membership,
    scope_from_path,
)
from shared_kernel.auth.permissions import Capability, Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreateIn(BaseModel):
    owner_type: Literal["user", "org"]
    owner_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)


class ProjectPatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    owner_type: str
    owner_id: uuid.UUID
    created_by_user_id: uuid.UUID
    version: int
    created_at: str
    deleted_at: str | None


class ProjectMemberOut(BaseModel):
    user_id: uuid.UUID
    role: str
    joined_at: str


class ProjectMemberPatchIn(BaseModel):
    role: Literal["owner", "member"]


class InviteCreateIn(BaseModel):
    email: EmailStr
    role: Literal["owner", "member"] = "member"


def _to_out(p) -> ProjectOut:  # type: ignore[no-untyped-def]
    owner_type = "org" if p.owner_org_id else "user"
    owner_id = p.owner_org_id or p.owner_user_id
    return ProjectOut(
        id=p.id, name=p.name,
        owner_type=owner_type, owner_id=owner_id,
        created_by_user_id=p.created_by_user_id,
        version=p.version,
        created_at=p.created_at.isoformat(),
        deleted_at=p.deleted_at.isoformat() if p.deleted_at else None,
    )


@router.get("")
async def list_projects(
    scope: Literal["user", "org"] = Query(...),
    id: uuid.UUID = Query(..., alias="id"),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[ProjectOut]:
    service = ProjectService(db)
    if scope == "user":
        if id != principal.user_id and not principal.is_admin:
            raise HTTPException(
                status_code=403, detail="user-scope project list is self-only",
            )
        rows = await service.list_by_user(id)
    else:
        # Org-scope requires caller membership (Admin bypass). Without this
        # check any authenticated user could enumerate every org's projects.
        from shared_kernel.auth.dependencies import get_role_resolver  # noqa: PLC0415
        from shared_kernel.auth.permissions import Scope as _Scope  # noqa: PLC0415
        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(principal, _Scope(org_id=id))
        if not principal.is_admin and not roles:
            raise HTTPException(
                status_code=403, detail="caller is not a member of this org",
            )
        rows = await service.list_by_org(id)
    return [_to_out(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreateIn,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ProjectOut:
    service = ProjectService(db)
    owner_type = ProjectOwnerType(body.owner_type)
    # The matrix capability differs per owner-type; check each manually here
    # so the scope_from_path builder can stay generic.
    from shared_kernel.auth.dependencies import get_role_resolver  # noqa: PLC0415
    from shared_kernel.auth.permissions import (  # noqa: PLC0415
        Scope,
        decide,
    )
    resolver = await get_role_resolver(db)
    cap = (
        Capability.PROJECT_CREATE_UNDER_ORG if owner_type is ProjectOwnerType.ORG
        else Capability.PROJECT_CREATE_UNDER_USER
    )
    scope = Scope(
        org_id=body.owner_id if owner_type is ProjectOwnerType.ORG else None,
    )
    decision = await decide(principal, cap, scope, resolver)
    if not decision.allowed:
        from shared_kernel.auth.dependencies import _raise_forbidden  # noqa: PLC0415
        _raise_forbidden(decision.reason)

    project = await service.create(
        owner_type=owner_type,
        owner_id=body.owner_id,
        name=body.name,
        created_by_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(project)


@router.get("/{project_id}")
async def read_project(
    project_id: uuid.UUID = Path(...),
    _=Depends(require_membership(project_param="project_id")),
    db: AsyncSession = Depends(db_session),
) -> ProjectOut:
    service = ProjectService(db)
    project = await service.get(project_id)
    return _to_out(project)


@router.patch("/{project_id}")
async def rename_project(
    body: ProjectPatchIn,
    project_id: uuid.UUID = Path(...),
    if_match: str = Header(..., alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(
        Capability.RESOURCE_CREATE_EDIT,
        scope_from_path(project_param="project_id"),
    )),
    db: AsyncSession = Depends(db_session),
) -> ProjectOut:
    try:
        expected = int(if_match.strip().strip('"'))
    except ValueError as exc:
        raise HTTPException(
            status_code=412, detail=f"invalid If-Match: {if_match!r}",
        ) from exc
    service = ProjectService(db)
    project = await service.rename(
        project_id=project_id, new_name=body.name, expected_version=expected,
        actor_user_id=principal.user_id, actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(
        Capability.PROJECT_DELETE,
        scope_from_path(project_param="project_id"),
    )),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = ProjectService(db)
    await service.soft_delete(
        project_id=project_id, actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip, request_id=ctx.request_id,
    )


@router.post("/{project_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
async def restore_project(
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    if not principal.is_admin:
        from shared_kernel.auth.dependencies import _raise_forbidden  # noqa: PLC0415
        _raise_forbidden("Admin only")
    service = ProjectService(db)
    await service.restore(
        project_id=project_id, admin_user_id=principal.user_id,
        actor_ip=ctx.actor_ip, request_id=ctx.request_id,
    )


@router.get("/{project_id}/members")
async def list_members(
    project_id: uuid.UUID = Path(...),
    _=Depends(require_membership(project_param="project_id")),
    db: AsyncSession = Depends(db_session),
) -> list[ProjectMemberOut]:
    service = ProjectService(db)
    members = await service.list_members(project_id)
    return [
        ProjectMemberOut(
            user_id=m.user_id, role=m.role.value,
            joined_at=m.joined_at.isoformat(),
        )
        for m in members
    ]


@router.delete(
    "/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT,
response_class=Response,
)
async def remove_project_member(
    project_id: uuid.UUID = Path(...),
    user_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(
        Capability.PROJECT_MEMBER_MANAGE,
        scope_from_path(project_param="project_id"),
    )),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = ProjectService(db)
    await service.remove_member(
        project_id=project_id, target_user_id=user_id,
        actor_user_id=principal.user_id, actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.patch("/{project_id}/members/{user_id}")
async def patch_project_member(
    body: ProjectMemberPatchIn,
    project_id: uuid.UUID = Path(...),
    user_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(
        Capability.PROJECT_MEMBER_MANAGE,
        scope_from_path(project_param="project_id"),
    )),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    service = ProjectService(db)
    await service.change_member_role(
        project_id=project_id, target_user_id=user_id,
        new_role=ProjectMemberRole(body.role),
        actor_user_id=principal.user_id, actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return {"ok": "true"}


@router.post("/{project_id}/invites", status_code=status.HTTP_201_CREATED)
async def create_project_invite(
    body: InviteCreateIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(
        Capability.PROJECT_MEMBER_MANAGE,
        scope_from_path(project_param="project_id"),
    )),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    service = InviteService(db)
    invited = await service.create_project_invite(
        project_id=project_id,
        inviter_user_id=principal.user_id,
        invitee_email=body.email,
        role=ProjectMemberRole(body.role),
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return {
        "id": str(invited.invite.id),
        "expires_at": invited.invite.expires_at.isoformat(),
    }


__all__ = ["router"]
