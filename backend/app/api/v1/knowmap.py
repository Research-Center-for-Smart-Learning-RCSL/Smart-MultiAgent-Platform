"""`/api/projects/{pid}/knowmap-configs` + `/api/knowmap-configs/{id}` — Knowledge Map (Phase 3).

The Axis-1 GraphRAG-over-documents surface (R11.12/R11.13/R11.14/R11.20). Splits into
a project-scoped collection router, an id-addressable item router, and a document
router — the same shape as the file-RAG surface (``rag.py``).

AuthZ mirrors file-RAG:
- List / read: project membership.
- Create / update / delete config, rebuild: ``RESOURCE_CREATE_EDIT`` at the config's project.
- Upload document + set allowlist: ``RESOURCE_CREATE_EDIT`` AND Project Owner (R10.10 analogue).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams
from contexts.knowledge.application.knowmap_config_service import (
    KnowmapConfigService,
    build_knowmap_embedder,
)
from contexts.knowledge.application.knowmap_ingest_service import (
    MAX_MULTIPART_BYTES,
    KnowmapIngestInput,
)
from contexts.knowledge.application.knowmap_triggers import enqueue_knowmap_build
from contexts.knowledge.domain.errors import DocumentTooLarge
from contexts.knowledge.domain.knowmap import KnowmapConfigDraft
from contexts.knowledge.domain.models import ChunkStrategy
from contexts.knowledge.infrastructure.knowmap_repositories import KnowmapDocumentRepository
from contexts.tenancy.interfaces.facade import TenancyFacade
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
from shared_kernel.validation import BoundedConfig

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class KnowmapConfigCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    builder_key_group_id: uuid.UUID
    chunk_strategy: Literal["fixed", "semantic"] = "fixed"
    chunk_params: BoundedConfig = Field(default_factory=dict)


class KnowmapConfigPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    builder_key_group_id: uuid.UUID | None = None
    chunk_params: BoundedConfig | None = None


class KnowmapConfigOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    builder_key_group_id: uuid.UUID
    chunk_strategy: str
    chunk_params: dict[str, Any]
    embed_provider: str | None
    embed_model: str | None
    embed_dim: int | None
    last_build_state: str
    last_build_at: str | None
    last_build_error: str | None
    created_at: str
    deleted_at: str | None


class KnowmapDocumentOut(BaseModel):
    id: uuid.UUID
    knowmap_config_id: uuid.UUID
    filename: str
    mime: str
    size_bytes: int
    sha256: str
    status: str
    scan_status: str
    uploaded_at: str
    agent_ids: list[uuid.UUID]


class KnowmapDocumentAgentsPatchIn(BaseModel):
    agent_ids: list[uuid.UUID] = Field(max_length=1_000)


def _to_config_out(c: Any) -> KnowmapConfigOut:
    return KnowmapConfigOut(
        id=c.id,
        project_id=c.project_id,
        name=c.name,
        builder_key_group_id=c.builder_key_group_id,
        chunk_strategy=c.chunk_strategy.value,
        chunk_params=c.chunk_params,
        embed_provider=c.embed_provider,
        embed_model=c.embed_model,
        embed_dim=c.embed_dim,
        last_build_state=c.last_build_state.value,
        last_build_at=c.last_build_at.isoformat() if c.last_build_at else None,
        last_build_error=c.last_build_error,
        created_at=c.created_at.isoformat(),
        deleted_at=c.deleted_at.isoformat() if c.deleted_at else None,
    )


def _to_document_out(d: Any) -> KnowmapDocumentOut:
    return KnowmapDocumentOut(
        id=d.id,
        knowmap_config_id=d.knowmap_config_id,
        filename=d.filename,
        mime=d.mime,
        size_bytes=d.size_bytes,
        sha256=d.sha256,
        status=d.status.value,
        scan_status=d.scan_status.value,
        uploaded_at=d.uploaded_at.isoformat(),
        agent_ids=list(d.agent_ids),
    )


async def validate_knowmap_agent_allowlist(
    *,
    db: AsyncSession,
    config_id: uuid.UUID,
    project_id: uuid.UUID,
    agent_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    """Ensure every id is a live agent in ``project_id`` bound to ``config_id``.

    A document's allowlist may only name agents that consume this Knowledge Map
    (``agent.knowmap_config_id == config_id``) — naming an unbound agent is a
    no-op at retrieval and an AuthZ smell. Returns the de-duplicated list; 422s.
    """
    from contexts.agents.interfaces.facade import AgentsFacade

    if not agent_ids:
        return []
    requested = set(agent_ids)
    bound = {
        a.id
        for a in await AgentsFacade(db).list_agents_for_project(project_id)
        if a.knowmap_config_id == config_id
    }
    unknown = requested - bound
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"agent_ids not bound to this config: {sorted(str(u) for u in unknown)}",
        )
    return list(requested)


async def _assert_project_membership(
    *, db: AsyncSession, principal: Principal, project_id: uuid.UUID
) -> None:
    if principal.is_admin:
        return
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope

    resolver = await get_role_resolver(db)
    roles = await resolver.roles_for(principal, Scope(project_id=project_id))
    if not roles:
        _raise_forbidden("caller is not a member of the config's project")


async def _assert_edit(*, db: AsyncSession, principal: Principal, project_id: uuid.UUID) -> None:
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal, Capability.RESOURCE_CREATE_EDIT, Scope(project_id=project_id), resolver
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)


async def _require_owner(*, db: AsyncSession, project_id: uuid.UUID, principal: Principal) -> None:
    if principal.is_admin:
        return
    from shared_kernel.auth.dependencies import _raise_forbidden

    if not await TenancyFacade(db).is_project_owner(principal.user_id, project_id):
        _raise_forbidden("Project Owner required to upload Knowledge Map documents")


# ---------------------------------------------------------------------------
# Project-scoped routes
# ---------------------------------------------------------------------------

project_router = APIRouter(prefix="/api/projects/{project_id}/knowmap-configs", tags=["knowmap"])


@project_router.get("")
async def list_knowmap_configs(
    project_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    _=Depends(require_membership(project_param="project_id")),
    db: AsyncSession = Depends(db_session),
) -> list[KnowmapConfigOut]:
    rows = await KnowmapConfigService(db).list_for_project(project_id)
    rows = rows[pagination.offset : pagination.offset + pagination.limit]
    return [_to_config_out(r) for r in rows]


@project_router.post("", status_code=status.HTTP_201_CREATED)
async def create_knowmap_config(
    body: KnowmapConfigCreateIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(Capability.RESOURCE_CREATE_EDIT, scope_from_path(project_param="project_id"))),
    db: AsyncSession = Depends(db_session),
) -> KnowmapConfigOut:
    draft = KnowmapConfigDraft(
        name=body.name,
        builder_key_group_id=body.builder_key_group_id,
        chunk_strategy=ChunkStrategy(body.chunk_strategy),
        chunk_params=body.chunk_params,
    )
    cfg = await KnowmapConfigService(db).create(
        project_id=project_id,
        draft=draft,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_config_out(cfg)


# ---------------------------------------------------------------------------
# Id-addressable routes
# ---------------------------------------------------------------------------

config_router = APIRouter(prefix="/api/knowmap-configs", tags=["knowmap"])


@config_router.get("/{config_id}")
async def read_knowmap_config(
    config_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> KnowmapConfigOut:
    cfg = await KnowmapConfigService(db).get(config_id)
    await _assert_project_membership(db=db, principal=principal, project_id=cfg.project_id)
    return _to_config_out(cfg)


@config_router.patch("/{config_id}")
async def patch_knowmap_config(
    body: KnowmapConfigPatchIn,
    config_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> KnowmapConfigOut:
    service = KnowmapConfigService(db)
    cfg = await service.get(config_id)
    await _assert_edit(db=db, principal=principal, project_id=cfg.project_id)
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        return _to_config_out(cfg)
    updated = await service.update(
        config_id=config_id,
        patch=patch,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_config_out(updated)


@config_router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_knowmap_config(
    config_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    """Soft-delete a Knowledge Map config and cascade its children (R11.20).

    DOM-4: the DB soft-delete + child-document removal + audit are committed
    first (point of no return); only then are the irreversible external stores
    (Neo4j subgraph, knowmap Qdrant points, MinIO blobs) purged best-effort.
    """
    service = KnowmapConfigService(db)
    cfg = await service.get(config_id)
    await _assert_edit(db=db, principal=principal, project_id=cfg.project_id)

    docs = await service.soft_delete(
        config_id=config_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()

    graph_outcome = await KnowmapConfigService.cascade_external_stores(
        config_id=config_id, project_id=cfg.project_id
    )
    blob_outcome = await KnowmapConfigService.purge_document_blobs(docs=docs)
    from shared_kernel import audit as _audit

    await _audit.emit(
        db,
        _audit.AuditEvent(
            action="knowmap.config_infra_purged",
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="knowmap_config",
            resource_id=config_id,
            metadata={"project_id": str(cfg.project_id), **graph_outcome, **blob_outcome},
            request_id=ctx.request_id,
        ),
    )


@config_router.post("/{config_id}/rebuild", status_code=status.HTTP_202_ACCEPTED)
async def rebuild_knowmap_config(
    config_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    """Explicit designer rebuild (Q-3/AC-6). Enqueues a ``knowmap_build`` with the
    dedup job id so a redundant click collapses onto an in-flight build."""
    service = KnowmapConfigService(db)
    cfg = await service.get(config_id)
    await _assert_edit(db=db, principal=principal, project_id=cfg.project_id)
    from shared_kernel import audit as _audit

    await _audit.emit(
        db,
        _audit.AuditEvent(
            action="knowmap.rebuild_requested",
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="knowmap_config",
            resource_id=config_id,
            metadata={"project_id": str(cfg.project_id)},
            request_id=ctx.request_id,
        ),
    )
    await db.commit()
    await enqueue_knowmap_build(
        config_id=cfg.id,
        last_build_state=cfg.last_build_state,
        last_build_at=cfg.last_build_at,
    )
    return {"status": "enqueued", "config_id": str(config_id)}


@config_router.get("/{config_id}/documents")
async def list_knowmap_documents(
    config_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[KnowmapDocumentOut]:
    cfg = await KnowmapConfigService(db).get(config_id)
    await _assert_project_membership(db=db, principal=principal, project_id=cfg.project_id)
    docs = await KnowmapDocumentRepository(db).list_for_config(
        config_id, limit=pagination.limit, offset=pagination.offset
    )
    return [_to_document_out(d) for d in docs]


@config_router.post("/{config_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_knowmap_document(
    config_id: uuid.UUID = Path(...),
    file: UploadFile = File(...),
    mime: str | None = Form(default=None),
    # Secure-by-default: an omitted allowlist means NO agent may see this
    # document's evidence. Edit later via PATCH /knowmap-documents/{id}/agents.
    agent_ids: list[uuid.UUID] = Form(default=[]),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> KnowmapDocumentOut:
    service = KnowmapConfigService(db)
    cfg = await service.get(config_id)
    await _assert_edit(db=db, principal=principal, project_id=cfg.project_id)
    await _require_owner(db=db, project_id=cfg.project_id, principal=principal)

    validated_agent_ids = await validate_knowmap_agent_allowlist(
        db=db, config_id=config_id, project_id=cfg.project_id, agent_ids=agent_ids
    )

    # Enforce the multipart cap before buffering the whole body (tus for larger).
    data = await file.read(MAX_MULTIPART_BYTES + 1)
    if len(data) > MAX_MULTIPART_BYTES:
        raise DocumentTooLarge(f"multipart upload exceeds {MAX_MULTIPART_BYTES} bytes; use tus")

    embedder = await build_knowmap_embedder(db, cfg)
    ingest = KnowmapConfigService.build_ingest_service(db, embedder=embedder)
    doc = await ingest.ingest(
        ipt=KnowmapIngestInput(
            knowmap_config_id=config_id,
            filename=file.filename or "upload",
            mime=mime or file.content_type or "application/octet-stream",
            data=data,
            uploaded_by=principal.user_id,
            agent_ids=tuple(validated_agent_ids),
        ),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_document_out(doc)


document_router = APIRouter(prefix="/api/knowmap-documents", tags=["knowmap"])


@document_router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_knowmap_document(
    document_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    """Hard-delete a Knowledge Map document, then purge its MinIO blob and enqueue
    a rebuild so the graph drops the document's triples (R11.20 / AC-7).

    DOM-7: a missing document and an existing-but-forbidden one both return 404 so
    the endpoint is not a cross-tenant UUID oracle. DOM-4: the row + audit commit
    before the irreversible blob purge.
    """
    from contexts.knowledge.domain.errors import KnowmapDocumentNotFound
    from shared_kernel import audit as _audit

    docs_repo = KnowmapDocumentRepository(db)
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowmap document not found")
    try:
        doc = await docs_repo.require(document_id)
    except KnowmapDocumentNotFound:
        raise not_found from None

    service = KnowmapConfigService(db)
    cfg = await service.get(doc.knowmap_config_id)
    from shared_kernel.auth.dependencies import get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal, Capability.RESOURCE_CREATE_EDIT, Scope(project_id=cfg.project_id), resolver
    )
    if not decision.allowed:
        raise not_found

    await docs_repo.delete(document_id)
    await _audit.emit(
        db,
        _audit.AuditEvent(
            action="knowmap.document_deleted",
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="knowmap_document",
            resource_id=document_id,
            metadata={
                "knowmap_config_id": str(doc.knowmap_config_id),
                "project_id": str(cfg.project_id),
                "filename": doc.filename,
            },
            request_id=ctx.request_id,
        ),
    )
    await db.commit()

    outcome = await KnowmapConfigService.purge_document_blobs(docs=[doc])
    await _audit.emit(
        db,
        _audit.AuditEvent(
            action="knowmap.document_infra_purged",
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="knowmap_document",
            resource_id=document_id,
            metadata={
                "knowmap_config_id": str(doc.knowmap_config_id),
                "project_id": str(cfg.project_id),
                **outcome,
            },
            request_id=ctx.request_id,
        ),
    )
    # A document-set change triggers a rebuild so the graph drops the removed
    # document's triples (its evidence is already hidden at retrieval by the
    # allowed-doc filter, which never returns a deleted document).
    await enqueue_knowmap_build(
        config_id=cfg.id, last_build_state=cfg.last_build_state, last_build_at=cfg.last_build_at
    )


@document_router.patch("/{document_id}/agents")
async def set_knowmap_document_agents(
    body: KnowmapDocumentAgentsPatchIn,
    document_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> KnowmapDocumentOut:
    """Replace a document's per-agent allowlist. Owner-gated (R10.10 analogue):
    re-scoping which agents see a document's evidence is an access-control
    decision. DOM-7: missing and forbidden both 404."""
    from contexts.knowledge.domain.errors import KnowmapDocumentNotFound
    from shared_kernel import audit as _audit
    from shared_kernel.auth.dependencies import get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    docs_repo = KnowmapDocumentRepository(db)
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowmap document not found")
    try:
        doc = await docs_repo.require(document_id)
    except KnowmapDocumentNotFound:
        raise not_found from None

    cfg = await KnowmapConfigService(db).get(doc.knowmap_config_id)
    resolver = await get_role_resolver(db)
    decision = await decide(
        principal, Capability.RESOURCE_CREATE_EDIT, Scope(project_id=cfg.project_id), resolver
    )
    if not decision.allowed:
        raise not_found
    await _require_owner(db=db, project_id=cfg.project_id, principal=principal)

    validated = await validate_knowmap_agent_allowlist(
        db=db, config_id=doc.knowmap_config_id, project_id=cfg.project_id, agent_ids=body.agent_ids
    )
    updated = await docs_repo.set_agents(document_id=document_id, agent_ids=validated)
    assert updated is not None
    await _audit.emit(
        db,
        _audit.AuditEvent(
            action="knowmap.document_agents_set",
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="knowmap_document",
            resource_id=document_id,
            metadata={
                "knowmap_config_id": str(doc.knowmap_config_id),
                "project_id": str(cfg.project_id),
                "agent_ids": [str(a) for a in validated],
            },
            request_id=ctx.request_id,
        ),
    )
    return _to_document_out(updated)


__all__ = [
    "config_router",
    "document_router",
    "project_router",
    "validate_knowmap_agent_allowlist",
]
