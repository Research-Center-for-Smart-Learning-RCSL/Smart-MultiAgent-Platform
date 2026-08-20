"""`/api/projects/*` — project CRUD + membership (§22.3)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.tenancy.application.invite_service import InviteService
from contexts.tenancy.application.project_service import (
    Project,
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
    owner_type: ProjectOwnerType
    owner_id: uuid.UUID
    created_by_user_id: uuid.UUID
    version: int
    created_at: str
    deleted_at: str | None


class ProjectMemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    role: ProjectMemberRole
    joined_at: str


class ProjectMemberPatchIn(BaseModel):
    role: Literal["owner", "member"]


class InviteCreateIn(BaseModel):
    email: EmailStr
    role: Literal["owner", "member"] = "member"


def _to_out(p) -> ProjectOut:
    owner_type = ProjectOwnerType.ORG if p.owner_org_id else ProjectOwnerType.USER
    owner_id = p.owner_org_id or p.owner_user_id
    return ProjectOut(
        id=p.id,
        name=p.name,
        owner_type=owner_type,
        owner_id=owner_id,
        created_by_user_id=p.created_by_user_id,
        version=p.version,
        created_at=p.created_at.isoformat(),
        deleted_at=p.deleted_at.isoformat() if p.deleted_at else None,
    )


async def _list_visible(
    db: AsyncSession,
    service: ProjectService,
    principal: Principal,
    *,
    org_id: uuid.UUID | None = None,
) -> list[Project]:
    """Every project the caller may see, optionally narrowed to one org (R13.32).

    `org_id` narrows the *result*; it never widens the candidate set. Serving the
    `?scope=org` branch from here rather than from a second query is the point:
    that branch previously called `list_by_org` behind a bare "is the caller in
    this org" check, so an org member asking for their own org received the name
    of every project in it — the disclosure this function's docstring claims to
    close, reachable by a query parameter, and the path the project list's per-org
    tab actually uses (`ProjectListView.vue`, `OrgProjectSwitcher.vue`).

    It used to return every project of every org the caller belonged to, with no
    project-membership check, so an org member could enumerate the name of every
    project in the org and walk into its workspaces from there.

    Now a candidate qualifies on one of two grounds: the caller's own standing in
    it (owner, member, or owner of the parent org), or holding at least one chat
    room the caller may read. The second test exists so that a room opened to the
    whole org (`allow_org_members`) still has an entry point — navigation runs
    project to workspace to room, and dropping non-member projects outright would
    strand every such room with no way to reach it.

    Composed here rather than inside either context: tenancy answers who the
    caller is, conversation answers which rooms they may read, and neither needs
    to learn the other's tables to do it.
    """
    candidates = await service.list_candidates_for_user(principal.user_id)
    in_scope = [p for p in candidates.projects if org_id is None or p.owner_org_id == org_id]
    undecided = [p.id for p in in_scope if p.id not in candidates.directly_visible_ids]
    with_visible_room: set[uuid.UUID] = set()
    if undecided:
        with_visible_room = await ConversationFacade(db).project_ids_with_visible_room(
            principal=principal,
            project_ids=undecided,
        )
    return [p for p in in_scope if p.id in candidates.directly_visible_ids or p.id in with_visible_room]


@router.get("")
async def list_projects(
    scope: Literal["user", "org"] | None = Query(None),
    owner_id: uuid.UUID | None = Query(None, alias="id"),
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[ProjectOut]:
    service = ProjectService(db)
    rows: Sequence[Project]
    if scope is None or owner_id is None:
        rows = await _list_visible(db, service, principal)
    elif scope == "user":
        if owner_id != principal.user_id and not principal.is_admin:
            raise HTTPException(
                status_code=403,
                detail="user-scope project list is self-only",
            )
        rows = await service.list_by_user(owner_id)
    else:
        from shared_kernel.auth.dependencies import get_role_resolver
        from shared_kernel.auth.permissions import Scope as _Scope

        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(principal, _Scope(org_id=owner_id))
        if not principal.is_admin and not roles:
            raise HTTPException(
                status_code=403,
                detail="caller is not a member of this org",
            )
        # Admin keeps the unfiltered org view, consistent with the bypass every
        # other gate gives them. Everyone else goes through the same visibility
        # rule as the unscoped list: this branch is a narrowing of that answer,
        # not a second way to ask the question.
        rows = (
            await service.list_by_org(owner_id)
            if principal.is_admin
            else await _list_visible(db, service, principal, org_id=owner_id)
        )
    rows = rows[pagination.offset : pagination.offset + pagination.limit]
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
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import (
        Scope,
        decide,
    )

    resolver = await get_role_resolver(db)
    cap = (
        Capability.PROJECT_CREATE_UNDER_ORG
        if owner_type is ProjectOwnerType.ORG
        else Capability.PROJECT_CREATE_UNDER_USER
    )
    scope = Scope(
        org_id=body.owner_id if owner_type is ProjectOwnerType.ORG else None,
    )
    decision = await decide(principal, cap, scope, resolver)
    if not decision.allowed:
        _raise_forbidden(decision.reason)

    # A user-owned project must be owned by the caller themselves. The
    # capability check above only proves "may create a user-owned project",
    # not "may create one *for this specific user*" — without this guard any
    # verified user could plant a project in a victim's account (SEC-2). An
    # admin may still create a project on behalf of another user.
    if owner_type is ProjectOwnerType.USER and body.owner_id != principal.user_id and not principal.is_admin:
        _raise_forbidden("a user-owned project must be owned by the caller")

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
    _=Depends(
        require(
            Capability.RESOURCE_CREATE_EDIT,
            scope_from_path(project_param="project_id"),
        )
    ),
    db: AsyncSession = Depends(db_session),
) -> ProjectOut:
    try:
        expected = int(if_match.strip().strip('"'))
    except ValueError as exc:
        raise HTTPException(
            status_code=412,
            detail=f"invalid If-Match: {if_match!r}",
        ) from exc
    service = ProjectService(db)
    project = await service.rename(
        project_id=project_id,
        new_name=body.name,
        expected_version=expected,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_project(
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(
        require(
            Capability.PROJECT_DELETE,
            scope_from_path(project_param="project_id"),
        )
    ),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = ProjectService(db)
    await service.soft_delete(
        project_id=project_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.post("/{project_id}/restore", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def restore_project(
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(
        require(
            Capability.PROJECT_DELETE,
            scope_from_path(project_param="project_id"),
        )
    ),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = ProjectService(db)
    await service.restore(
        project_id=project_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.get("/{project_id}/members")
async def list_members(
    project_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    _=Depends(require_membership(project_param="project_id")),
    db: AsyncSession = Depends(db_session),
) -> list[ProjectMemberOut]:
    service = ProjectService(db)
    all_members = await service.list_members(project_id)
    members = all_members[pagination.offset : pagination.offset + pagination.limit]
    user_ids = [m.user_id for m in members]
    if user_ids:
        from contexts.identity.infrastructure import tables as user_t

        email_rows = (
            await db.execute(
                sa.select(user_t.users.c.id, user_t.users.c.email).where(user_t.users.c.id.in_(user_ids))
            )
        ).all()
        emails: dict[uuid.UUID, str] = {r.id: r.email for r in email_rows}
    else:
        emails = {}
    return [
        ProjectMemberOut(
            user_id=m.user_id,
            email=emails.get(m.user_id, ""),
            role=m.role,
            joined_at=m.joined_at.isoformat(),
        )
        for m in members
    ]


@router.delete(
    "/{project_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def remove_project_member(
    project_id: uuid.UUID = Path(...),
    user_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(
        require(
            Capability.PROJECT_MEMBER_MANAGE,
            scope_from_path(project_param="project_id"),
        )
    ),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = ProjectService(db)
    await service.remove_member(
        project_id=project_id,
        target_user_id=user_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.patch("/{project_id}/members/{user_id}")
async def patch_project_member(
    body: ProjectMemberPatchIn,
    project_id: uuid.UUID = Path(...),
    user_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(
        require(
            Capability.PROJECT_MEMBER_MANAGE,
            scope_from_path(project_param="project_id"),
        )
    ),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    service = ProjectService(db)
    await service.change_member_role(
        project_id=project_id,
        target_user_id=user_id,
        new_role=ProjectMemberRole(body.role),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return {"ok": "true"}


@router.post("/{project_id}/invites", status_code=status.HTTP_201_CREATED)
async def create_project_invite(
    body: InviteCreateIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(
        require(
            Capability.PROJECT_MEMBER_MANAGE,
            scope_from_path(project_param="project_id"),
        )
    ),
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
