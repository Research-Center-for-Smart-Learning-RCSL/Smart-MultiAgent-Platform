"""`/api/projects/{id}/member-groups` + `/api/member-groups/*` — §13.2a.

Member Groups narrow chat-room visibility below project level. Managing them
requires capability #14 (Invite/remove Project Member), the same authority that
decides who is in the project at all — no new row was added to the §5.2 matrix,
because group membership is not a role ([R5.06], [R13.30]).

Every id-addressed route resolves the parent project from the group row and
authorises against that. A client-supplied project id is never trusted.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams, require_if_match
from contexts.tenancy.application.member_group_service import MemberGroupService
from contexts.tenancy.domain.errors import MemberGroupNotFound
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    _raise_forbidden,
    current_context,
    current_principal,
    get_role_resolver,
    require,
    require_membership,
    scope_from_path,
)
from shared_kernel.auth.permissions import Capability, Principal, Scope, decide
from shared_kernel.db.session import db_session

project_router = APIRouter(prefix="/api/projects", tags=["member-groups"])
group_router = APIRouter(prefix="/api/member-groups", tags=["member-groups"])


class MemberGroupCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberGroupPatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberGroupOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    version: int
    created_at: str


class MemberGroupMemberIn(BaseModel):
    user_id: uuid.UUID


class MemberGroupMemberOut(BaseModel):
    user_id: uuid.UUID
    joined_at: str


def _to_out(group) -> MemberGroupOut:
    return MemberGroupOut(
        id=group.id,
        project_id=group.project_id,
        name=group.name,
        version=group.version,
        created_at=group.created_at.isoformat(),
    )


async def _may_manage(db: AsyncSession, principal: Principal, project_id: uuid.UUID) -> bool:
    """Capability #14 on the parent project. Admin passes."""
    if principal.is_admin:
        return True
    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.PROJECT_MEMBER_MANAGE,
        Scope(project_id=project_id),
        resolver,
    )
    return decision.allowed


async def _resolve_readable(
    db: AsyncSession,
    principal: Principal,
    group_id: uuid.UUID,
):
    """Load a group the caller is allowed to know exists (R13.31).

    A non-manager who is not in the group gets `MemberGroupNotFound`, not a 403:
    the point of the narrowing is that other groups' existence is not disclosed,
    and a 403 discloses it.
    """
    service = MemberGroupService(db)
    group = await service.get(group_id)
    if await _may_manage(db, principal, group.project_id):
        return group, True
    if not await service.is_visible_to(group=group, user_id=principal.user_id):
        raise MemberGroupNotFound(str(group_id))
    return group, False


# --------------------------------------------------------------------------- #
# Project-scoped collection
# --------------------------------------------------------------------------- #


@project_router.get("/{project_id}/member-groups")
async def list_member_groups(
    project_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    _=Depends(require_membership(project_param="project_id")),
    db: AsyncSession = Depends(db_session),
) -> list[MemberGroupOut]:
    """R13.31 — a manager sees the project's groups, anyone else sees their own."""
    groups = await MemberGroupService(db).list_for_project(
        project_id=project_id,
        caller_user_id=principal.user_id,
        caller_is_manager=await _may_manage(db, principal, project_id),
    )
    page = groups[pagination.offset : pagination.offset + pagination.limit]
    return [_to_out(g) for g in page]


@project_router.post("/{project_id}/member-groups", status_code=status.HTTP_201_CREATED)
async def create_member_group(
    body: MemberGroupCreateIn,
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
) -> MemberGroupOut:
    group = await MemberGroupService(db).create(
        project_id=project_id,
        name=body.name,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(group)


# --------------------------------------------------------------------------- #
# Group-scoped
# --------------------------------------------------------------------------- #


@group_router.get("/{group_id}")
async def read_member_group(
    group_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> MemberGroupOut:
    group, _is_manager = await _resolve_readable(db, principal, group_id)
    return _to_out(group)


@group_router.patch("/{group_id}")
async def rename_member_group(
    body: MemberGroupPatchIn,
    group_id: uuid.UUID = Path(...),
    if_match: str = Header(..., alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> MemberGroupOut:
    service = MemberGroupService(db)
    group = await service.get(group_id)
    if not await _may_manage(db, principal, group.project_id):
        _raise_forbidden("member group management requires project member management")
    updated = await service.rename(
        group_id=group_id,
        new_name=body.name,
        expected_version=require_if_match(if_match),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(updated)


@group_router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_member_group(
    group_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = MemberGroupService(db)
    group = await service.get(group_id)
    if not await _may_manage(db, principal, group.project_id):
        _raise_forbidden("member group management requires project member management")
    await service.delete(
        group_id=group_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@group_router.get("/{group_id}/members")
async def list_member_group_members(
    group_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[MemberGroupMemberOut]:
    """A member of the group may see who else is in it; anyone else cannot see
    that the group exists at all (R13.31)."""
    await _resolve_readable(db, principal, group_id)
    members = await MemberGroupService(db).list_members(group_id)
    return [MemberGroupMemberOut(user_id=m.user_id, joined_at=m.joined_at.isoformat()) for m in members]


@group_router.post(
    "/{group_id}/members",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def add_member_group_member(
    body: MemberGroupMemberIn,
    group_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = MemberGroupService(db)
    group = await service.get(group_id)
    if not await _may_manage(db, principal, group.project_id):
        _raise_forbidden("member group management requires project member management")
    await service.add_member(
        group_id=group_id,
        user_id=body.user_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@group_router.delete(
    "/{group_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def remove_member_group_member(
    group_id: uuid.UUID = Path(...),
    user_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = MemberGroupService(db)
    group = await service.get(group_id)
    if not await _may_manage(db, principal, group.project_id):
        _raise_forbidden("member group management requires project member management")
    await service.remove_member(
        group_id=group_id,
        user_id=user_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


__all__ = ["group_router", "project_router"]
