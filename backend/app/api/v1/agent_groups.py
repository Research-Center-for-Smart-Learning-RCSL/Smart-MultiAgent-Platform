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

from app.api.v1.deps import PaginationParams, assert_project_membership, assert_project_owner
from contexts.agent_groups.domain.errors import AgentGroupNotFound
from contexts.agent_groups.domain.models import AgentGroup
from contexts.agent_groups.interfaces.facade import AgentGroupFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session


class AgentGroupCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AgentGroupUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AgentGroupOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    # Phase 4α: surfaced so the group panel renders the privacy toggle's current
    # state without a second call. ``concept_map_enabled`` is the wide-layer
    # Concept Map opt-in (R11.10); mutated via the dedicated toggle endpoint.
    concept_map_enabled: bool
    created_at: str


def _to_out(group: AgentGroup) -> AgentGroupOut:
    return AgentGroupOut(
        id=group.id,
        project_id=group.project_id,
        name=group.name,
        concept_map_enabled=group.concept_map_enabled,
        created_at=group.created_at.isoformat(),
    )


class AgentGroupMemberIn(BaseModel):
    agent_id: uuid.UUID


class AgentGroupMembersOut(BaseModel):
    members: list[uuid.UUID]


class ConceptMapEnabledIn(BaseModel):
    enabled: bool


class ConceptMapStatusOut(BaseModel):
    group_id: uuid.UUID
    concept_map_enabled: bool


async def _assert_project_owner(*, db: AsyncSession, principal: Principal, project_id: uuid.UUID) -> None:
    await assert_project_owner(
        db=db,
        principal=principal,
        project_id=project_id,
        reason="only a project owner may manage agent groups",
    )


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


@project_router.get("")
async def list_groups(
    project_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[AgentGroupOut]:
    """List a project's agent groups (Phase 4α). Read ⇒ project membership."""
    await assert_project_membership(db=db, principal=principal, project_id=project_id)
    groups = await AgentGroupFacade(db).list_groups(project_id)
    groups = groups[pagination.offset : pagination.offset + pagination.limit]
    return [_to_out(g) for g in groups]


@project_router.post("", status_code=status.HTTP_201_CREATED)
async def create_group(
    body: AgentGroupCreateIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AgentGroupOut:
    await _assert_project_owner(db=db, principal=principal, project_id=project_id)
    facade = AgentGroupFacade(db)
    group_id = await facade.create_group(
        project_id=project_id,
        name=body.name,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    group = await facade.get_group(group_id)
    if group is None:  # pragma: no cover - just-created row is always live
        raise AgentGroupNotFound(str(group_id))
    return _to_out(group)


# ---------------------------------------------------------------------------
# Group-scoped: member management
# ---------------------------------------------------------------------------

group_router = APIRouter(prefix="/api/agent-groups", tags=["agent-groups"])


@group_router.get("/{group_id}")
async def get_group(
    group_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AgentGroupOut:
    """Read a single agent group (Phase 4α). Read ⇒ project membership."""
    project_id = await _group_project_id(db, group_id)
    await assert_project_membership(db=db, principal=principal, project_id=project_id)
    group = await AgentGroupFacade(db).get_group(group_id)
    if group is None:
        raise AgentGroupNotFound(str(group_id))
    return _to_out(group)


@group_router.patch("/{group_id}")
async def rename_group(
    body: AgentGroupUpdateIn,
    group_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AgentGroupOut:
    """Rename an agent group (Phase 4α) — strict Project-Owner only."""
    project_id = await _group_project_id(db, group_id)
    await _assert_project_owner(db=db, principal=principal, project_id=project_id)
    group = await AgentGroupFacade(db).rename_group(
        group_id=group_id,
        name=body.name,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(group)


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


@group_router.delete(
    "/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_group(
    group_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    """Delete an agent group (WS6 R11.20) — strict Project-Owner only.

    Purges the group-owned Concept Map's Neo4j subgraph + Qdrant points as part
    of the delete so no external-store data is orphaned (AC-10). Member rows are
    left in place; the group tombstone makes them inert on every read path.
    """
    project_id = await _group_project_id(db, group_id)
    await _assert_project_owner(db=db, principal=principal, project_id=project_id)

    from app.api.v1._graphrag_owner_cascade import (
        purge_owner_graph_configs_external,
        soft_delete_owner_graph_configs,
    )
    from contexts.knowledge.interfaces.facade import KnowledgeFacade

    configs = await soft_delete_owner_graph_configs(
        KnowledgeFacade(db),
        owner_kind="agent_group",
        owner_id=group_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await AgentGroupFacade(db).soft_delete(
        group_id=group_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    # DOM-4: commit the group + config soft-deletes before purging external stores.
    await db.commit()
    await purge_owner_graph_configs_external(
        db,
        configs=configs,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@group_router.get("/{group_id}/members")
async def list_members(
    group_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AgentGroupMembersOut:
    project_id = await _group_project_id(db, group_id)
    await assert_project_membership(db=db, principal=principal, project_id=project_id)
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
