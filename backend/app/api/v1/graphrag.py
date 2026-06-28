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

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams
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
from contexts.knowledge.interfaces.facade import KnowledgeFacade
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


class GraphRagConfigCreateIn(BaseModel):
    agent_id: uuid.UUID
    builder_key_group_id: uuid.UUID
    trigger_config: GraphRagTriggerConfig = Field(default_factory=GraphRagTriggerConfig)


class GraphRagConfigPatchIn(BaseModel):
    """Partial update — both fields optional. Omitted = unchanged."""

    builder_key_group_id: uuid.UUID | None = None
    trigger_config: GraphRagTriggerConfig | None = None


class GraphRagConfigOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    agent_id: uuid.UUID
    builder_key_group_id: uuid.UUID
    trigger_config: dict[str, object]
    last_build_state: str
    last_build_at: str | None
    last_build_error: str | None
    created_at: str
    deleted_at: str | None


class GraphRagStatusOut(BaseModel):
    id: uuid.UUID
    state: str
    last_build_at: str | None
    last_build_error: str | None


class GraphRagBuildOut(BaseModel):
    accepted: bool
    build_id: uuid.UUID | None
    state: str


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


def _to_out(cfg: GraphRagConfig) -> GraphRagConfigOut:
    return GraphRagConfigOut(
        id=cfg.id,
        project_id=cfg.project_id,
        agent_id=cfg.agent_id,
        builder_key_group_id=cfg.builder_key_group_id,
        trigger_config=cfg.trigger_config,
        last_build_state=cfg.last_build_state.value,
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
        agent_id=body.agent_id,
        builder_key_group_id=body.builder_key_group_id,
        trigger_config=body.trigger_config.model_dump(exclude_none=True),
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


async def _assert_project_membership(
    *,
    db: AsyncSession,
    principal: Principal,
    project_id: uuid.UUID,
) -> None:
    from shared_kernel.auth.dependencies import (
        _raise_forbidden,
        get_role_resolver,
    )
    from shared_kernel.auth.permissions import Scope

    if principal.is_admin:
        return
    resolver = await get_role_resolver(db)
    roles = await resolver.roles_for(principal, Scope(project_id=project_id))
    if not roles:
        _raise_forbidden("caller is not a member of the config's project")


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


@config_router.get("/{config_id}")
async def read_config(
    config_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> GraphRagConfigOut:
    service = GraphRagConfigService(db)
    cfg = await service.get(config_id)
    await _assert_project_membership(
        db=db,
        principal=principal,
        project_id=cfg.project_id,
    )
    return _to_out(cfg)


@config_router.get("/{config_id}/status")
async def read_status(
    config_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> GraphRagStatusOut:
    service = GraphRagConfigService(db)
    cfg = await service.get(config_id)
    await _assert_project_membership(
        db=db,
        principal=principal,
        project_id=cfg.project_id,
    )
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
    await _assert_project_membership(
        db=db,
        principal=principal,
        project_id=cfg.project_id,
    )
    view = await facade.get_graphrag_graph(config_id, limit=limit)
    return GraphOut(
        config_id=view.config_id,
        nodes=[
            GraphNodeOut(id=n.name, degree=n.degree, build_id=n.build_id, type=n.type)
            for n in view.nodes
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
    # DOM-4: commit the soft delete + audit row before any external delete.
    await db.commit()

    # Best-effort cascade of Neo4j subgraph + Qdrant entity vectors.
    outcome = await GraphRagConfigService.cascade_external_stores(
        config_id=config_id,
        project_id=cfg.project_id,
    )

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
            state=cfg.last_build_state.value,
        )

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
    await enqueue("graphrag_build", config_id=str(config_id), triggered_by="manual")
    return GraphRagBuildOut(
        accepted=True,
        build_id=None,
        state=cfg.last_build_state.value,
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


__all__ = ["admin_router", "config_router", "project_router"]
