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

import uuid

from fastapi import Response, APIRouter, Depends, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings

from contexts.knowledge.application.graphrag_config_service import (
    GraphRagConfigService,
)
from contexts.knowledge.domain.graphrag import (
    GraphRagConfig,
    GraphRagConfigDraft,
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


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class GraphRagConfigCreateIn(BaseModel):
    agent_id: uuid.UUID
    builder_key_group_id: uuid.UUID
    trigger_config: dict[str, object] = Field(default_factory=dict)


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


def _to_out(cfg: GraphRagConfig) -> GraphRagConfigOut:
    return GraphRagConfigOut(
        id=cfg.id,
        project_id=cfg.project_id,
        agent_id=cfg.agent_id,
        builder_key_group_id=cfg.builder_key_group_id,
        trigger_config=cfg.trigger_config,
        last_build_state=cfg.last_build_state.value,
        last_build_at=(
            cfg.last_build_at.isoformat() if cfg.last_build_at else None
        ),
        last_build_error=cfg.last_build_error,
        created_at=cfg.created_at.isoformat(),
        deleted_at=cfg.deleted_at.isoformat() if cfg.deleted_at else None,
    )


# ---------------------------------------------------------------------------
# Project-scoped routes
# ---------------------------------------------------------------------------

project_router = APIRouter(
    prefix="/api/projects/{project_id}/graphrag-configs", tags=["graphrag"],
)


@project_router.get("")
async def list_configs(
    project_id: uuid.UUID = Path(...),
    _=Depends(require_membership(project_param="project_id")),
    db: AsyncSession = Depends(db_session),
) -> list[GraphRagConfigOut]:
    service = GraphRagConfigService(db)
    rows = await service.list_for_project(project_id)
    return [_to_out(r) for r in rows]


@project_router.post("", status_code=status.HTTP_201_CREATED)
async def create_config(
    body: GraphRagConfigCreateIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(
        Capability.RESOURCE_CREATE_EDIT,
        scope_from_path(project_param="project_id"),
    )),
    db: AsyncSession = Depends(db_session),
) -> GraphRagConfigOut:
    service = GraphRagConfigService(db)
    draft = GraphRagConfigDraft(
        agent_id=body.agent_id,
        builder_key_group_id=body.builder_key_group_id,
        trigger_config=dict(body.trigger_config),
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
    from shared_kernel.auth.dependencies import (  # noqa: PLC0415
        _raise_forbidden, get_role_resolver,
    )
    from shared_kernel.auth.permissions import Scope  # noqa: PLC0415
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
    from shared_kernel.auth.dependencies import (  # noqa: PLC0415
        _raise_forbidden, get_role_resolver,
    )
    from shared_kernel.auth.permissions import Scope, decide  # noqa: PLC0415
    resolver = await get_role_resolver(db)
    decision = await decide(
        principal, Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=project_id), resolver,
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
        db=db, principal=principal, project_id=cfg.project_id,
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
        db=db, principal=principal, project_id=cfg.project_id,
    )
    payload = await service.status(config_id)
    return GraphRagStatusOut(
        id=uuid.UUID(payload["id"]),
        state=payload["state"],
        last_build_at=payload["last_build_at"],
        last_build_error=payload["last_build_error"],
    )


@config_router.delete(
    "/{config_id}", status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_config(
    config_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = GraphRagConfigService(db)
    cfg = await service.get(config_id)
    await _assert_edit(db=db, principal=principal, project_id=cfg.project_id)
    await service.soft_delete(
        config_id=config_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    # Cascade the Neo4j subgraph (§22.8). Kept inside the request lifecycle
    # rather than a worker so the client knows the delete is complete.
    from contexts.knowledge.infrastructure.neo4j_driver import (  # noqa: PLC0415
        Neo4jAsyncDriver,
    )
    from app.config.settings import get_settings  # noqa: PLC0415
    settings = get_settings()
    neo4j_conf = getattr(settings, "neo4j", None)
    if neo4j_conf is not None:
        driver = Neo4jAsyncDriver(
            uri=neo4j_conf.url,
            auth=(neo4j_conf.user, neo4j_conf.password),
        )
        try:
            await driver.delete_all(config_id=config_id)
        finally:
            await driver.close()


@config_router.post(
    "/{config_id}/build", status_code=status.HTTP_202_ACCEPTED,
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
    from arq.connections import RedisSettings, create_pool  # noqa: PLC0415
    from shared_kernel import audit as _audit  # noqa: PLC0415

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
    pool = await create_pool(
        RedisSettings.from_dsn(get_settings().redis.dsn),
    )
    try:
        await pool.enqueue_job(
            "graphrag_build",
            config_id=str(config_id),
            triggered_by="manual",
        )
    finally:
        await pool.close()
    return GraphRagBuildOut(
        accepted=True, build_id=None, state=cfg.last_build_state.value,
    )


# ---------------------------------------------------------------------------
# Admin override (R11a.02)
# ---------------------------------------------------------------------------

admin_router = APIRouter(
    prefix="/api/admin/graphrag", tags=["graphrag-admin"],
)


@admin_router.post("/{config_id}/reset")
async def admin_reset(
    config_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> GraphRagConfigOut:
    if not principal.is_admin:
        from shared_kernel.auth.dependencies import _raise_forbidden  # noqa: PLC0415
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
