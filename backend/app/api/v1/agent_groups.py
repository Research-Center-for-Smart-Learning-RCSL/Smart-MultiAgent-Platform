"""`/api/projects/{pid}/agent-groups` + `/api/agent-groups/{id}/members` (Phase 2b WS2).

The owner-centric surface for multi-member agent groups: a group is created and
its members managed here, then referenced as a GraphRAG config owner
(``owner_kind="agent_group"``). Group membership is a per-project trust boundary
(it gates who contributes to and reads a shared Concept Map), so mutations are
restricted to a strict Project Owner; reads require project membership.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agent_groups.domain.errors import AgentGroupNotFound
from contexts.agent_groups.interfaces.facade import AgentGroupFacade
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session


class AgentGroupCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AgentGroupOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str


class AgentGroupMemberIn(BaseModel):
    agent_id: uuid.UUID


class AgentGroupMembersOut(BaseModel):
    members: list[uuid.UUID]


class ConceptMapEnabledIn(BaseModel):
    enabled: bool


class ConceptMapStatusOut(BaseModel):
    group_id: uuid.UUID
    concept_map_enabled: bool


async def _assert_project_membership(
    *, db: AsyncSession, principal: Principal, project_id: uuid.UUID
) -> None:
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope

    if principal.is_admin:
        return
    resolver = await get_role_resolver(db)
    roles = await resolver.roles_for(principal, Scope(project_id=project_id))
    if not roles:
        _raise_forbidden("caller is not a member of the group's project")


async def _assert_project_owner(*, db: AsyncSession, principal: Principal, project_id: uuid.UUID) -> None:
    from shared_kernel.auth.dependencies import _raise_forbidden

    if principal.is_admin:
        return
    if not await TenancyFacade(db).is_project_owner(principal.user_id, project_id):
        _raise_forbidden("only a project owner may manage agent groups")


async def _group_project_id(db: AsyncSession, group_id: uuid.UUID) -> uuid.UUID:
    project_id = await AgentGroupFacade(db).group_project_id(group_id)
    if project_id is None:
        raise AgentGroupNotFound(str(group_id))
    return project_id


# ---------------------------------------------------------------------------
# Project-scoped: create
# ---------------------------------------------------------------------------

project_router = APIRouter(
    prefix="/api/projects/{project_id}/agent-groups",
    tags=["agent-groups"],
)


@project_router.post("", status_code=status.HTTP_201_CREATED)
async def create_group(
    body: AgentGroupCreateIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AgentGroupOut:
    await _assert_project_owner(db=db, principal=principal, project_id=project_id)
    group_id = await AgentGroupFacade(db).create_group(
        project_id=project_id,
        name=body.name,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return AgentGroupOut(id=group_id, project_id=project_id, name=body.name)


# ---------------------------------------------------------------------------
# Group-scoped: member management
# ---------------------------------------------------------------------------

group_router = APIRouter(prefix="/api/agent-groups", tags=["agent-groups"])


@group_router.put("/{group_id}/concept-map-enabled")
async def set_concept_map_enabled(
    body: ConceptMapEnabledIn,
    group_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ConceptMapStatusOut:
    """Toggle the group's Concept Map privacy opt-in (R11.10) — Project-Owner only."""
    project_id = await _group_project_id(db, group_id)
    await _assert_project_owner(db=db, principal=principal, project_id=project_id)
    await AgentGroupFacade(db).set_concept_map_enabled(
        group_id=group_id,
        enabled=body.enabled,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return ConceptMapStatusOut(group_id=group_id, concept_map_enabled=body.enabled)


@group_router.get("/{group_id}/members")
async def list_members(
    group_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AgentGroupMembersOut:
    project_id = await _group_project_id(db, group_id)
    await _assert_project_membership(db=db, principal=principal, project_id=project_id)
    members = await AgentGroupFacade(db).list_members(group_id)
    return AgentGroupMembersOut(members=list(members))


@group_router.post("/{group_id}/members", status_code=status.HTTP_201_CREATED)
async def add_member(
    body: AgentGroupMemberIn,
    group_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AgentGroupMembersOut:
    project_id = await _group_project_id(db, group_id)
    await _assert_project_owner(db=db, principal=principal, project_id=project_id)
    facade = AgentGroupFacade(db)
    await facade.add_member(
        group_id=group_id,
        agent_id=body.agent_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    members = await facade.list_members(group_id)
    return AgentGroupMembersOut(members=list(members))


@group_router.delete(
    "/{group_id}/members/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def remove_member(
    group_id: uuid.UUID = Path(...),
    agent_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    project_id = await _group_project_id(db, group_id)
    await _assert_project_owner(db=db, principal=principal, project_id=project_id)
    await AgentGroupFacade(db).remove_member(
        group_id=group_id,
        agent_id=agent_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


__all__ = ["group_router", "project_router"]
