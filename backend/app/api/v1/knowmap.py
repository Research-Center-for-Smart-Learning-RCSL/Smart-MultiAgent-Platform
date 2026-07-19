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
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams, assert_project_membership
from app.api.v1.deps import validate_agent_allowlist as _validate_agent_allowlist_generic
from contexts.knowledge.application.knowmap_config_service import (
    KnowmapConfigService,
    build_knowmap_embedder,
)
from contexts.knowledge.application.knowmap_graph_service import (
    DEFAULT_GRAPH_LIMIT,
    MAX_GRAPH_LIMIT,
)
from contexts.knowledge.application.knowmap_ingest_service import (
    MAX_MULTIPART_BYTES,
    KnowmapIngestInput,
)
from contexts.knowledge.application.knowmap_triggers import enqueue_knowmap_build
from contexts.knowledge.domain.embedding_pin import TeardownOutcome
from contexts.knowledge.domain.errors import DocumentTooLarge, KnowmapConfigNotFound
from contexts.knowledge.domain.graphrag import IN_FLIGHT_BUILD_STATES, BuildState
from contexts.knowledge.domain.knowmap import KnowmapConfigDraft
from contexts.knowledge.domain.models import ChunkStrategy, DocumentStatus, ScanStatus
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapConfigRepository,
    KnowmapDocumentRepository,
)
from contexts.knowledge.interfaces.facade import KnowledgeFacade
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


class KnowmapRebuildAck(BaseModel):
    status: Literal["enqueued"]
    config_id: uuid.UUID


class KnowmapConfigPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    builder_key_group_id: uuid.UUID | None = None
    chunk_params: BoundedConfig | None = None


class KnowmapConfigOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    builder_key_group_id: uuid.UUID
    chunk_strategy: ChunkStrategy
    chunk_params: dict[str, Any]
    embed_provider: str | None
    embed_model: str | None
    embed_dim: int | None
    last_build_state: BuildState
    last_build_at: str | None
    last_build_error: str | None
    created_at: str
    deleted_at: str | None


class KnowmapConfigPatchOut(KnowmapConfigOut):
    """PATCH response — the config plus any agents auto-detached by a builder
    key-group change that collided with their consumer group (R11.25 / F-14).

    Dedicated subclass so the shared GET/create ``KnowmapConfigOut`` shape stays
    unbroadened. ``detached_agent_ids`` is empty on any non-colliding change.
    """

    detached_agent_ids: list[uuid.UUID] = Field(default_factory=list)


class KnowmapDocumentOut(BaseModel):
    id: uuid.UUID
    knowmap_config_id: uuid.UUID
    filename: str
    mime: str
    size_bytes: int
    sha256: str
    status: DocumentStatus
    scan_status: ScanStatus
    uploaded_at: str
    agent_ids: list[uuid.UUID]


class KnowmapDocumentAgentsPatchIn(BaseModel):
    agent_ids: list[uuid.UUID] = Field(max_length=1_000)


class KnowmapGraphNodeOut(BaseModel):
    id: str
    degree: int
    build_id: str | None
    type: str


class KnowmapGraphEdgeOut(BaseModel):
    source: str
    relation: str
    target: str
    confidence: float


class KnowmapGraphOut(BaseModel):
    config_id: uuid.UUID
    nodes: list[KnowmapGraphNodeOut]
    edges: list[KnowmapGraphEdgeOut]
    truncated: bool


def _to_config_out(c: Any) -> KnowmapConfigOut:
    return KnowmapConfigOut(
        id=c.id,
        project_id=c.project_id,
        name=c.name,
        builder_key_group_id=c.builder_key_group_id,
        chunk_strategy=c.chunk_strategy,
        chunk_params=c.chunk_params,
        embed_provider=c.embed_provider,
        embed_model=c.embed_model,
        embed_dim=c.embed_dim,
        last_build_state=c.last_build_state,
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
        status=d.status,
        scan_status=d.scan_status,
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
    Thin wrapper over the shared implementation in ``deps.py`` (was a hand
    copy of rag.py's equivalent, differing only in the config-id attribute
    name — found in code review, 2026-07-10).
    """
    return await _validate_agent_allowlist_generic(
        db=db,
        config_id=config_id,
        project_id=project_id,
        agent_ids=agent_ids,
        config_id_attr="knowmap_config_id",
    )


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
    await assert_project_membership(db=db, principal=principal, project_id=cfg.project_id)
    return _to_config_out(cfg)


@config_router.patch("/{config_id}")
async def patch_knowmap_config(
    body: KnowmapConfigPatchIn,
    config_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> KnowmapConfigPatchOut:
    service = KnowmapConfigService(db)
    cfg = await service.get(config_id)
    await _assert_edit(db=db, principal=principal, project_id=cfg.project_id)
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        return KnowmapConfigPatchOut(**_to_config_out(cfg).model_dump(), detached_agent_ids=[])
    updated, detached_agent_ids = await service.update(
        config_id=config_id,
        patch=patch,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    base = _to_config_out(updated)
    return KnowmapConfigPatchOut(**base.model_dump(), detached_agent_ids=detached_agent_ids)


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
    # F-3: if that was the project's last Knowledge Map config, drop the now-orphan
    # collection and release the durable pin — in that order, and only if Qdrant
    # confirms the collection is gone. The teardown takes the (project, knowmap) lock
    # and re-checks for live configs itself.
    teardown = await service.teardown_orphan_collection(project_id=cfg.project_id)
    from shared_kernel import audit as _audit

    await _audit.emit(
        db,
        _audit.AuditEvent(
            action="knowmap.config_infra_purged",
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="knowmap_config",
            resource_id=config_id,
            metadata={
                "project_id": str(cfg.project_id),
                # F-3: true only for a Qdrant-confirmed drop; `collection_teardown`
                # carries the full outcome.
                "collection_dropped": teardown is TeardownOutcome.DROPPED,
                "collection_teardown": teardown.value,
                **graph_outcome,
                **blob_outcome,
            },
            request_id=ctx.request_id,
        ),
    )


@config_router.post("/{config_id}/rebuild", status_code=status.HTTP_202_ACCEPTED)
async def rebuild_knowmap_config(
    config_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> KnowmapRebuildAck:
    """Explicit designer rebuild (Q-3/AC-6). Advances the corpus revision and
    enqueues a ``knowmap_build`` for it, so an operator-requested rebuild always
    produces a fresh build generation rather than colliding with a retained
    prior-build result (F-12 W2)."""
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
    # F-12 (W2): an explicit rebuild requests a fresh build generation. Advance the
    # corpus revision in this transaction so the build's job id cannot collide with
    # a retained result of the previous build. Without it, arq drops the re-enqueue
    # as a duplicate for up to keep_result (3600s) whenever the corpus is unchanged
    # since the last build — and the reconciler does not heal a terminal FAILED
    # state, so manual recovery would silently no-op.
    new_revision = await KnowmapConfigRepository(db).bump_corpus_revision(config_id)
    await db.commit()
    await enqueue_knowmap_build(config_id=cfg.id, target_revision=new_revision)
    return KnowmapRebuildAck(status="enqueued", config_id=config_id)


@config_router.get("/{config_id}/graph")
async def read_knowmap_graph(
    config_id: uuid.UUID = Path(...),
    limit: int = Query(DEFAULT_GRAPH_LIMIT, ge=1, le=MAX_GRAPH_LIMIT),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> KnowmapGraphOut:
    """Read a bounded view of the config's knowledge graph (Phase 3β, R11.24).

    AuthZ matches ``read_knowmap_config``: membership at the config's project.
    The limit is clamped so a large graph can never stream an unbounded
    payload to the browser — mirrors ``graphrag.py``'s ``read_graph``, which
    this route now matches structurally too: through ``KnowledgeFacade``
    rather than instantiating application-layer services directly (found in
    code review — this file's other routes still bypass the facade, a
    pre-existing pattern this one deliberately does not extend further).
    """
    facade = KnowledgeFacade(db)
    cfg = await facade.get_knowmap_config(config_id)
    if cfg is None:
        raise KnowmapConfigNotFound(str(config_id))
    await assert_project_membership(db=db, principal=principal, project_id=cfg.project_id)
    if cfg.last_build_state in IN_FLIGHT_BUILD_STATES:
        # Mirrors graphrag.py's read_graph and the shared turn-context retrieval gate:
        # a mid-2PC or irrecoverable graph is not served to either consumer.
        return KnowmapGraphOut(config_id=config_id, nodes=[], edges=[], truncated=False)
    view = await facade.get_knowmap_graph(config_id, limit=limit)
    return KnowmapGraphOut(
        config_id=view.config_id,
        nodes=[
            KnowmapGraphNodeOut(id=n.name, degree=n.degree, build_id=n.build_id, type=n.type)
            for n in view.nodes
        ],
        edges=[
            KnowmapGraphEdgeOut(
                source=e.source,
                relation=e.relation,
                target=e.target,
                confidence=e.confidence,
            )
            for e in view.edges
        ],
        truncated=view.truncated,
    )


@config_router.get("/{config_id}/documents")
async def list_knowmap_documents(
    config_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[KnowmapDocumentOut]:
    cfg = await KnowmapConfigService(db).get(config_id)
    await assert_project_membership(db=db, principal=principal, project_id=cfg.project_id)
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
    # F-12: a document delete changes the buildable corpus — bump the revision in
    # the same transaction so the rebuild below gets a job id distinct from any
    # prior corpus state's and is never dropped as a duplicate.
    new_revision = await KnowmapConfigRepository(db).bump_corpus_revision(doc.knowmap_config_id)
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
    await enqueue_knowmap_build(config_id=cfg.id, target_revision=new_revision)


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
    # DOM-7 applies to the owner gate too, not just the capability check:
    # `_require_owner` raises a distinguishing 403 (worded for the upload
    # route it was written for), which would let a capability-holding
    # non-owner tell "document exists, I lack access" from "not found". Mask
    # both the same way.
    if not principal.is_admin and not await TenancyFacade(db).is_project_owner(
        principal.user_id, cfg.project_id
    ):
        raise not_found

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


# ---------------------------------------------------------------------------
# Admin override (R11a.02)
# ---------------------------------------------------------------------------

admin_router = APIRouter(
    prefix="/api/admin/knowmap",
    tags=["knowmap-admin"],
)


@admin_router.post("/{config_id}/reset")
async def admin_reset(
    config_id: uuid.UUID = Path(...),
    force: bool = Query(
        default=False,
        description=(
            "Override lock contention and, when 2PC compensation cannot complete, "
            "still force idle while recording the incomplete outcome (R11a.02)."
        ),
    ),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> KnowmapConfigOut:
    """Mirrors graphrag.py's admin_reset, down to the platform-admin check.

    Knowledge Maps had no reset until
    docs/tasks/2026-07-17-graphrag-reset-expired-recovery/, which made
    ``recovery_unavailable`` reachable here — a state outside the reconciler's sweep
    set, so without this route an operator's only option would have been a rebuild.

    AuthZ deliberately matches the Concept Map's: platform admin, no project scoping.
    That is weaker than every other route in this module (FU-2 in the dossier), and
    diverging here would have made the two resets inconsistent while fixing neither.
    """
    if not principal.is_admin:
        from shared_kernel.auth.dependencies import _raise_forbidden

        _raise_forbidden("R11a.02 admin reset requires platform admin")
    service = KnowmapConfigService(db)
    cfg = await service.admin_reset(
        config_id=config_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        force=force,
        request_id=ctx.request_id,
    )
    return _to_config_out(cfg)


__all__ = [
    "admin_router",
    "config_router",
    "document_router",
    "project_router",
    "validate_knowmap_agent_allowlist",
]
