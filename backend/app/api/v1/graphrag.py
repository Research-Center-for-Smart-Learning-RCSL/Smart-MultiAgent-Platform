"""`/api/projects/{pid}/graphrag-configs` + `/api/graphrag/{id}` — E.7/E.8 (§22.8).

Routers:
- ``project_router`` — collection scoped to a project (list, create).
- ``config_router`` — id-addressable item (read, delete-with-cascade,
  manual build, status).
- ``admin_router`` — platform-admin reset (R11a.02).

AuthZ mirrors :mod:`app.api.v1.rag`: list/read/status ⇒ membership;
create/delete/build ⇒ ``RESOURCE_CREATE_EDIT`` at the config's project;
admin reset ⇒ ``principal.is_admin``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal, cast

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams, assert_project_membership
from contexts.knowledge.application.graphrag_config_service import (
    GraphRagConfigService,
)
from contexts.knowledge.application.graphrag_graph_service import (
    DEFAULT_GRAPH_LIMIT,
    MAX_GRAPH_LIMIT,
)
from contexts.knowledge.domain.graphrag import (
    BuildState,
    GraphRagConfig,
    GraphRagConfigDraft,
)
from contexts.knowledge.interfaces.config_access import has_config_read_access
from contexts.knowledge.interfaces.facade import KnowledgeFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    _raise_forbidden,
    current_context,
    current_principal,
    require,
    require_membership,
    scope_from_path,
)
from shared_kernel.auth.permissions import Capability, Principal
from shared_kernel.db.session import db_session

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class GraphRagTriggerConfig(BaseModel):
    """Typed GraphRAG build-trigger config (audit M11).

    Replaces the previously untyped ``dict[str, object]`` that was persisted
    verbatim as JSON. Unknown keys are rejected and numeric bounds are
    enforced so arbitrary/oversized payloads can't be stored.
    """

    model_config = ConfigDict(extra="forbid")

    every_n_messages: int | None = Field(default=None, ge=1, le=100_000)
    silence_minutes: int | None = Field(default=None, ge=1, le=100_000)
    manual: bool = False


OwnerKind = Literal["agent_group", "chatroom", "workspace"]


class GraphRagConfigCreateIn(BaseModel):
    """Owner-centric create (Phase 2b WS2).

    ``owner_id`` references an existing owner of ``owner_kind`` in the project:
    an ``agent_group`` (managed via the member-CRUD surface), a ``chatroom``, or
    a ``workspace``.
    """

    owner_kind: OwnerKind
    owner_id: uuid.UUID
    builder_key_group_id: uuid.UUID
    trigger_config: GraphRagTriggerConfig = Field(default_factory=GraphRagTriggerConfig)
    # Phase 2b WS5 (R11.21) — optional recency half-life in days; None inherits
    # the platform default. Must be positive and finite when supplied
    # (allow_inf_nan=False rejects Infinity, which gt=0 alone would admit).
    recency_half_life_days: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class GraphRagConfigPatchIn(BaseModel):
    """Partial update — all fields optional. Omitted = unchanged."""

    builder_key_group_id: uuid.UUID | None = None
    trigger_config: GraphRagTriggerConfig | None = None
    recency_half_life_days: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class GraphRagConfigOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    owner_kind: OwnerKind
    owner_id: uuid.UUID
    # Owner display name (Phase 4α) — for the project-scoped overview; ``None`` if
    # the owner was concurrently deleted between the config read and the name join.
    owner_name: str | None
    # Derived owning agent — the sole member for a singleton agent_group; ``None``
    # for a multi-member group or a chatroom/workspace owner.
    agent_id: uuid.UUID | None
    builder_key_group_id: uuid.UUID
    trigger_config: dict[str, object]
    recency_half_life_days: float | None
    last_build_state: BuildState
    last_build_at: str | None
    last_build_error: str | None
    created_at: str
    deleted_at: str | None


class ConceptMapOwnerOptionOut(BaseModel):
    """A selectable owner for the Concept Maps overview create picker (Phase 4α)."""

    owner_kind: OwnerKind
    owner_id: uuid.UUID
    owner_name: str


class GraphRagStatusOut(BaseModel):
    id: uuid.UUID
    state: BuildState
    last_build_at: str | None
    last_build_error: str | None


class AgentConceptMapCoverageEntryOut(BaseModel):
    """One Concept Map covering an agent (Phase 4α R11.09, read-only)."""

    config_id: uuid.UUID
    owner_kind: OwnerKind
    owner_id: uuid.UUID
    owner_name: str
    # Whether the map currently feeds the agent's retrieval: chatroom maps always
    # do; a wide (agent_group / workspace) map only when its owner has enabled it.
    active: bool
    last_build_state: BuildState
    last_build_at: str | None
    last_build_error: str | None


class AgentConceptMapCoverageOut(BaseModel):
    agent_id: uuid.UUID
    entries: list[AgentConceptMapCoverageEntryOut]


class GraphRagBuildOut(BaseModel):
    accepted: bool
    build_id: uuid.UUID | None
    state: BuildState


class GraphNodeOut(BaseModel):
    id: str
    degree: int
    build_id: str | None
    type: str


class GraphEdgeOut(BaseModel):
    source: str
    relation: str
    target: str
    confidence: float


class GraphOut(BaseModel):
    """Bounded node/edge view for the knowledge-graph visualizer (viz P0)."""

    config_id: uuid.UUID
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    truncated: bool


def _owner_id(cfg: GraphRagConfig) -> uuid.UUID:
    return {
        "chatroom": cfg.owner_chatroom_id,
        "agent_group": cfg.owner_agent_group_id,
        "workspace": cfg.owner_workspace_id,
    }[cfg.owner_kind]  # type: ignore[return-value]


def _to_out(cfg: GraphRagConfig) -> GraphRagConfigOut:
    return GraphRagConfigOut(
        id=cfg.id,
        project_id=cfg.project_id,
        owner_kind=cast(OwnerKind, cfg.owner_kind),
        owner_id=_owner_id(cfg),
        owner_name=cfg.owner_name,
        agent_id=cfg.agent_id,
        builder_key_group_id=cfg.builder_key_group_id,
        trigger_config=cfg.trigger_config,
        recency_half_life_days=cfg.recency_half_life_days,
        last_build_state=cfg.last_build_state,
        last_build_at=(cfg.last_build_at.isoformat() if cfg.last_build_at else None),
        last_build_error=cfg.last_build_error,
        created_at=cfg.created_at.isoformat(),
        deleted_at=cfg.deleted_at.isoformat() if cfg.deleted_at else None,
    )


# ---------------------------------------------------------------------------
# Project-scoped routes
# ---------------------------------------------------------------------------

project_router = APIRouter(
    prefix="/api/projects/{project_id}/graphrag-configs",
    tags=["graphrag"],
)


@project_router.get("")
async def list_configs(
    project_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    _=Depends(require_membership(project_param="project_id")),
    db: AsyncSession = Depends(db_session),
) -> list[GraphRagConfigOut]:
    service = GraphRagConfigService(db)
    rows = await service.list_for_project(project_id)
    rows = rows[pagination.offset : pagination.offset + pagination.limit]
    return [_to_out(r) for r in rows]


@project_router.get("/owner-options")
async def list_owner_options(
    project_id: uuid.UUID = Path(...),
    _=Depends(require_membership(project_param="project_id")),
    db: AsyncSession = Depends(db_session),
) -> list[ConceptMapOwnerOptionOut]:
    """Owners in the project without a Concept Map, for the overview create picker."""
    options = await KnowledgeFacade(db).list_concept_map_owner_options(project_id)
    return [
        ConceptMapOwnerOptionOut(
            owner_kind=cast(OwnerKind, o.owner_kind),
            owner_id=o.owner_id,
            owner_name=o.owner_name,
        )
        for o in options
    ]


@project_router.post("", status_code=status.HTTP_201_CREATED)
async def create_config(
    body: GraphRagConfigCreateIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(
        require(
            Capability.RESOURCE_CREATE_EDIT,
            scope_from_path(project_param="project_id"),
        )
    ),
    db: AsyncSession = Depends(db_session),
) -> GraphRagConfigOut:
    service = GraphRagConfigService(db)
    draft = GraphRagConfigDraft(
        owner_kind=body.owner_kind,
        owner_id=body.owner_id,
        builder_key_group_id=body.builder_key_group_id,
        trigger_config=body.trigger_config.model_dump(exclude_none=True),
        recency_half_life_days=body.recency_half_life_days,
    )
    cfg = await service.create(
        project_id=project_id,
        draft=draft,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(cfg)


# ---------------------------------------------------------------------------
# Id-addressable routes
# ---------------------------------------------------------------------------

config_router = APIRouter(prefix="/api/graphrag", tags=["graphrag"])


async def _assert_edit(
    *,
    db: AsyncSession,
    principal: Principal,
    project_id: uuid.UUID,
) -> None:
    from shared_kernel.auth.dependencies import (
        _raise_forbidden,
        get_role_resolver,
    )
    from shared_kernel.auth.permissions import Scope, decide

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)


async def _assert_config_read(
    *,
    db: AsyncSession,
    principal: Principal,
    cfg: GraphRagConfig,
) -> None:
    """R11.17 owner-aware read gate for a Concept Map config.

    Chatroom-owned configs go through the owning room's ACL; agent_group /
    workspace configs require project membership plus the owner's
    ``concept_map_enabled`` opt-in. Admin bypasses (inside the predicate).
    Raises 403 on denial. Replaces the bare project-membership check that let a
    room-denied project member read a private room's graph/status.
    """
    if not await has_config_read_access(db, principal=principal, cfg=cfg):
        _raise_forbidden("caller cannot read this concept map")


@config_router.get("/{config_id}")
async def read_config(
    config_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> GraphRagConfigOut:
    service = GraphRagConfigService(db)
    cfg = await service.get(config_id)
    await _assert_config_read(db=db, principal=principal, cfg=cfg)
    return _to_out(cfg)


@config_router.get("/{config_id}/status")
async def read_status(
    config_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> GraphRagStatusOut:
    service = GraphRagConfigService(db)
    cfg = await service.get(config_id)
    await _assert_config_read(db=db, principal=principal, cfg=cfg)
    payload = await service.status(config_id)
    return GraphRagStatusOut(
        id=uuid.UUID(payload["id"]),
        state=payload["state"],
        last_build_at=payload["last_build_at"],
        last_build_error=payload["last_build_error"],
    )


@config_router.get("/{config_id}/graph")
async def read_graph(
    config_id: uuid.UUID = Path(...),
    limit: int = Query(DEFAULT_GRAPH_LIMIT, ge=1, le=MAX_GRAPH_LIMIT),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> GraphOut:
    """Read a bounded view of the config's knowledge graph (viz P0).

    AuthZ matches ``read_config``: membership at the config's project. The
    facade assembles the read model; the limit is clamped so a large graph can
    never stream an unbounded payload to the browser.
    """
    facade = KnowledgeFacade(db)
    cfg = await facade.get_graphrag_config(config_id)
    if cfg is None:
        from contexts.knowledge.domain.errors import GraphRagConfigNotFound

        raise GraphRagConfigNotFound(str(config_id))
    await _assert_config_read(db=db, principal=principal, cfg=cfg)
    view = await facade.get_graphrag_graph(config_id, limit=limit)
    return GraphOut(
        config_id=view.config_id,
        nodes=[
            GraphNodeOut(id=n.name, degree=n.degree, build_id=n.build_id, type=n.type) for n in view.nodes
        ],
        edges=[
            GraphEdgeOut(
                source=e.source,
                relation=e.relation,
                target=e.target,
                confidence=e.confidence,
            )
            for e in view.edges
        ],
        truncated=view.truncated,
    )


@config_router.patch("/{config_id}")
async def update_config(
    body: GraphRagConfigPatchIn,
    config_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> GraphRagConfigOut:
    """R11.05 — edit trigger config or builder key group.

    AuthZ matches DELETE: ``RESOURCE_CREATE_EDIT`` at the config's project.
    """
    service = GraphRagConfigService(db)
    cfg = await service.get(config_id)
    await _assert_edit(db=db, principal=principal, project_id=cfg.project_id)
    refreshed = await service.update(
        config_id=config_id,
        builder_key_group_id=body.builder_key_group_id,
        trigger_config=(
            body.trigger_config.model_dump(exclude_none=True) if body.trigger_config is not None else None
        ),
        recency_half_life_days=body.recency_half_life_days,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(refreshed)


@config_router.delete(
    "/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_config(
    config_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    """§22.8 — soft-delete a GraphRAG config and cascade its external stores.

    DOM-2: the config's entity vectors live in the shared
    ``graphrag_{project_id}`` Qdrant collection tagged with ``config_id``.
    The old code cascaded only the Neo4j subgraph, so those vectors leaked
    forever — and, being in a collection shared with sibling configs, kept
    surfacing in their retrieval. We now delete them via ``delete_by_config``.

    DOM-4: the soft delete + audit row are committed *first* — that commit is
    the point of no return — and only then are the irreversible Neo4j +
    Qdrant deletes attempted, best-effort, recorded in a follow-up audit row.
    """
    service = GraphRagConfigService(db)
    cfg = await service.get(config_id)
    await _assert_edit(db=db, principal=principal, project_id=cfg.project_id)
    await service.soft_delete(
        config_id=config_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    # F-11 (W1): if that was the project's last Concept Map config, clear the durable
    # pin in this same transaction so it commits atomically with the soft-delete —
    # a concurrent create at a different dimension can never observe a
    # config-deleted-but-pin-present state and be spuriously rejected.
    pin_cleared = await service.clear_pin_if_last_config(project_id=cfg.project_id)
    # DOM-4: commit the soft delete + audit row before any external delete.
    await db.commit()

    # Best-effort cascade of Neo4j subgraph + Qdrant entity vectors.
    outcome = await GraphRagConfigService.cascade_external_stores(
        config_id=config_id,
        project_id=cfg.project_id,
    )
    # F-11 (W5): drop the now-orphan collection after the commit (DOM-4), re-checked
    # under the lock so a concurrent re-pin keeps its collection.
    collection_dropped = pin_cleared and await service.drop_orphan_collection(project_id=cfg.project_id)

    # Follow-up audit row recording the infra outcome (DOM-4) — committed by
    # the db_session dependency.
    from shared_kernel import audit as _audit

    await _audit.emit(
        db,
        _audit.AuditEvent(
            action="graphrag.infra_purged",
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="graphrag_config",
            resource_id=config_id,
            metadata={
                "project_id": str(cfg.project_id),
                "collection_dropped": collection_dropped,
                **outcome,
            },
            request_id=ctx.request_id,
        ),
    )


@config_router.post(
    "/{config_id}/build",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_build(
    config_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> GraphRagBuildOut:
    service = GraphRagConfigService(db)
    cfg = await service.get(config_id)
    await _assert_edit(db=db, principal=principal, project_id=cfg.project_id)

    # Audit M8: only enqueue when the config is in a resumable state. The
    # builder serializes execution via the per-config Redis lock, but without
    # this guard each duplicate POST still enqueues a job that spins up a worker
    # only to fail acquiring the lock — a member with edit rights could flood
    # the queue. An in-progress build returns accepted=False with its state.
    if cfg.last_build_state not in {BuildState.IDLE, BuildState.FAILED}:
        return GraphRagBuildOut(
            accepted=False,
            build_id=None,
            state=cfg.last_build_state,
        )

    from contexts.knowledge.application.graphrag_triggers import graphrag_build_job_id
    from shared_kernel import audit as _audit
    from shared_kernel.queue import enqueue

    await _audit.emit(
        db,
        _audit.AuditEvent(
            action="graphrag.build_requested",
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="graphrag_config",
            resource_id=config_id,
            metadata={"trigger": "manual"},
            request_id=ctx.request_id,
        ),
    )
    # D5: share the auto-trigger dedup id so a manual click while an auto build
    # for the same config+watermark is queued collapses onto the one job.
    await enqueue(
        "graphrag_build",
        config_id=str(config_id),
        triggered_by="manual",
        _job_id=graphrag_build_job_id(
            config_id,
            last_build_state=cfg.last_build_state,
            last_build_at=cfg.last_build_at,
        ),
    )
    return GraphRagBuildOut(
        accepted=True,
        build_id=None,
        state=cfg.last_build_state.value,
    )


# ---------------------------------------------------------------------------
# Agent-scoped read: Concept Map coverage (Phase 4α R11.09)
# ---------------------------------------------------------------------------

agent_router = APIRouter(prefix="/api/agents", tags=["graphrag"])


@agent_router.get("/{agent_id}/concept-map-coverage")
async def read_agent_concept_map_coverage(
    agent_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AgentConceptMapCoverageOut:
    """Read-only coverage: every Concept Map that could feed an agent's retrieval.

    Transparency, not an attach (R11.09) — the maps of the agent's chatrooms, the
    agent_groups it belongs to, and those chatrooms' workspaces, each flagged with
    whether it is currently active. AuthZ matches ``read_config``: membership at
    the agent's project.
    """
    from contexts.agents.domain.errors import AgentNotFound
    from contexts.agents.interfaces.facade import AgentsFacade

    agent = await AgentsFacade(db).get_agent(agent_id)
    if agent is None:
        raise AgentNotFound(str(agent_id))
    await assert_project_membership(
        db=db,
        principal=principal,
        project_id=agent.project_id,
    )
    coverage = await KnowledgeFacade(db).list_agent_concept_map_coverage(agent_id)
    return AgentConceptMapCoverageOut(
        agent_id=agent_id,
        entries=[
            AgentConceptMapCoverageEntryOut(
                config_id=e.config.id,
                owner_kind=cast(OwnerKind, e.config.owner_kind),
                owner_id=_owner_id(e.config),
                owner_name=e.owner_name,
                active=e.active,
                last_build_state=e.config.last_build_state,
                last_build_at=(e.config.last_build_at.isoformat() if e.config.last_build_at else None),
                last_build_error=e.config.last_build_error,
            )
            for e in coverage
        ],
    )


# ---------------------------------------------------------------------------
# Admin override (R11a.02)
# ---------------------------------------------------------------------------

admin_router = APIRouter(
    prefix="/api/admin/graphrag",
    tags=["graphrag-admin"],
)


@admin_router.post("/{config_id}/reset")
async def admin_reset(
    config_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> GraphRagConfigOut:
    if not principal.is_admin:
        from shared_kernel.auth.dependencies import _raise_forbidden

        _raise_forbidden("R11a.02 admin reset requires platform admin")
    service = GraphRagConfigService(db)
    cfg = await service.admin_reset(
        config_id=config_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(cfg)


__all__ = ["admin_router", "agent_router", "config_router", "project_router"]
