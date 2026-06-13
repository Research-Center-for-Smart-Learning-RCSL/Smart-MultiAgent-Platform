"""`/api/projects/{pid}/rag-configs` + `/api/rag-configs/{id}` — RAG surface (§22.7).

Splits into two routers: project-scoped collection, id-addressable item.

AuthZ:
- List / read: project membership.
- Create / delete config: `RESOURCE_CREATE_EDIT`.
- Upload document: `RESOURCE_CREATE_EDIT` AND the caller must be a
  **Project Owner** at the config's project (R10.10). This is enforced
  by checking `ProjectMemberRole.OWNER` after the generic capability
  guard.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
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
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.keys.infrastructure.adapters import build_router
from contexts.knowledge.application.config_service import RagConfigService
from contexts.knowledge.application.ingest_service import (
    MAX_MULTIPART_BYTES,
    IngestInput,
    IngestService,
)
from contexts.knowledge.domain.errors import DocumentTooLarge
from contexts.knowledge.domain.models import (
    ChunkStrategy,
    RagConfigDraft,
    RagDocument,
)
from contexts.knowledge.infrastructure.blob_store import MinioBlobStore
from contexts.knowledge.infrastructure.embedders import router_embedder_for
from contexts.knowledge.infrastructure.qdrant_store import QdrantStore
from contexts.tenancy.domain.models import ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import ProjectMemberRepository
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


class RagConfigCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    chunk_strategy: Literal["fixed", "semantic"]
    chunk_params: dict[str, Any] = Field(default_factory=dict)
    embed_key_id: uuid.UUID
    embed_provider: Literal["openai", "gemini", "voyage"]
    embed_model: str
    rerank_enabled: bool = False
    rerank_key_id: uuid.UUID | None = None
    rerank_provider: Literal["cohere"] | None = None
    rerank_model: str | None = None
    top_k: int = Field(default=8, gt=0, le=100)


class RagConfigOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    chunk_strategy: str
    chunk_params: dict[str, Any]
    embed_key_id: uuid.UUID | None
    embed_provider: str
    embed_model: str
    rerank_enabled: bool
    rerank_key_id: uuid.UUID | None
    rerank_provider: str | None
    rerank_model: str | None
    top_k: int
    created_at: str
    deleted_at: str | None


class RagDocumentOut(BaseModel):
    id: uuid.UUID
    rag_config_id: uuid.UUID
    filename: str
    mime: str
    size_bytes: int
    sha256: str
    status: str
    scan_status: str
    uploaded_at: str


def _to_config_out(c) -> RagConfigOut:
    return RagConfigOut(
        id=c.id,
        project_id=c.project_id,
        name=c.name,
        chunk_strategy=c.chunk_strategy.value,
        chunk_params=c.chunk_params,
        embed_key_id=c.embed_key_id,
        embed_provider=c.embed_provider,
        embed_model=c.embed_model,
        rerank_enabled=c.rerank_enabled,
        rerank_key_id=c.rerank_key_id,
        rerank_provider=c.rerank_provider,
        rerank_model=c.rerank_model,
        top_k=c.top_k,
        created_at=c.created_at.isoformat(),
        deleted_at=c.deleted_at.isoformat() if c.deleted_at else None,
    )


def _to_document_out(d) -> RagDocumentOut:
    return RagDocumentOut(
        id=d.id,
        rag_config_id=d.rag_config_id,
        filename=d.filename,
        mime=d.mime,
        size_bytes=d.size_bytes,
        sha256=d.sha256,
        status=d.status.value,
        scan_status=d.scan_status.value,
        uploaded_at=d.uploaded_at.isoformat(),
    )


_log = logging.getLogger(__name__)


async def _purge_documents_infra(
    *,
    project_id: uuid.UUID,
    docs: Sequence[RagDocument],
) -> dict[str, Any]:
    """Best-effort removal of Qdrant points + MinIO blobs for ``docs``.

    MUST be called only *after* the DB transaction that removed the document
    rows has committed (DOM-4): infra deletes are irreversible, so the audit
    trail has to be durable first. Every failure is swallowed and reflected
    in the returned summary, which the caller writes to a follow-up audit
    row — so the destructive infra step is itself always recorded.
    """
    summary: dict[str, Any] = {
        "documents": len(docs),
        "qdrant_purged": True,
        "blobs_removed": 0,
        "blobs_failed": 0,
    }
    if not docs:
        return summary

    settings = get_settings()

    # Qdrant points — one batched delete keyed on doc_id.
    try:
        qclient = AsyncQdrantClient(
            url=settings.qdrant.url,
            api_key=settings.qdrant.api_key or None,
        )
        try:
            await QdrantStore(qclient).delete_documents(
                project_id=project_id,
                document_ids=[d.id for d in docs],
            )
        finally:
            await qclient.close()
    except Exception:
        summary["qdrant_purged"] = False
        _log.exception("rag infra purge: qdrant delete failed for project %s", project_id)

    # MinIO blobs — one remove_object per document.
    minio = None
    try:
        from minio import Minio

        minio = Minio(
            settings.minio.endpoint,
            access_key=settings.minio.root_access_key,
            secret_key=settings.minio.root_secret_key,
            secure=settings.minio.use_tls,
            region=settings.minio.region,
        )
    except Exception:
        _log.exception("rag infra purge: minio client init failed")

    for d in docs:
        if minio is None:
            summary["blobs_failed"] += 1
            continue
        try:
            # minio_path is "<bucket>/<key>"; split on the first slash.
            bucket, _, key = d.minio_path.partition("/")
            if bucket and key:
                await asyncio.to_thread(minio.remove_object, bucket, key)
                summary["blobs_removed"] += 1
            else:
                summary["blobs_failed"] += 1
        except Exception:
            summary["blobs_failed"] += 1
            _log.exception("rag infra purge: minio remove failed for doc %s", d.id)

    return summary


# ---------------------------------------------------------------------------
# Project-scoped routes
# ---------------------------------------------------------------------------

project_router = APIRouter(
    prefix="/api/projects/{project_id}/rag-configs",
    tags=["rag"],
)


@project_router.get("")
async def list_rag_configs(
    project_id: uuid.UUID = Path(...),
    _=Depends(require_membership(project_param="project_id")),
    db: AsyncSession = Depends(db_session),
) -> list[RagConfigOut]:
    service = RagConfigService(db)
    rows = await service.list_for_project(project_id)
    return [_to_config_out(r) for r in rows]


@project_router.post("", status_code=status.HTTP_201_CREATED)
async def create_rag_config(
    body: RagConfigCreateIn,
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
) -> RagConfigOut:
    service = RagConfigService(db)
    draft = RagConfigDraft(
        name=body.name,
        chunk_strategy=ChunkStrategy(body.chunk_strategy),
        chunk_params=body.chunk_params,
        embed_key_id=body.embed_key_id,
        embed_provider=body.embed_provider,
        embed_model=body.embed_model,
        rerank_enabled=body.rerank_enabled,
        rerank_key_id=body.rerank_key_id,
        rerank_provider=body.rerank_provider,
        rerank_model=body.rerank_model,
        top_k=body.top_k,
    )
    cfg = await service.create(
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

config_router = APIRouter(prefix="/api/rag-configs", tags=["rag"])


async def _require_owner(
    *,
    db: AsyncSession,
    project_id: uuid.UUID,
    principal: Principal,
) -> None:
    if principal.is_admin:
        return
    from shared_kernel.auth.dependencies import _raise_forbidden

    members = ProjectMemberRepository(db)
    member = await members.get(project_id=project_id, user_id=principal.user_id)
    if member is None or member.role is not ProjectMemberRole.OWNER:
        _raise_forbidden("R10.10 requires Project Owner to upload RAG documents")


@config_router.get("/{config_id}")
async def read_rag_config(
    config_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> RagConfigOut:
    service = RagConfigService(db)
    cfg = await service.get(config_id)
    # Membership check scoped to cfg's project.
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope

    if not principal.is_admin:
        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(principal, Scope(project_id=cfg.project_id))
        if not roles:
            _raise_forbidden("caller is not a member of the config's project")
    return _to_config_out(cfg)


@config_router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_rag_config(
    config_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    """§22.7 — soft-delete a RAG config and cascade its children.

    DOM-1: a config's child documents/chunks/vectors/blobs are not removed
    by the soft delete on its own row. ``RagConfigService.soft_delete``
    hard-deletes the document rows (``rag_chunks`` cascade via FK); this
    endpoint then commits and purges the Qdrant points + MinIO blobs
    best-effort. Ordering matters (DOM-4): the commit is the point of no
    return, so the destructive infra step always trails a durable audit row.
    """
    service = RagConfigService(db)
    cfg = await service.get(config_id)
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=cfg.project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)

    docs = await service.soft_delete(
        config_id=config_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    # DOM-4: commit the config + document removals and their audit row before
    # touching any external store.
    await db.commit()

    # Best-effort purge of the cascaded documents' Qdrant points + blobs.
    outcome = await _purge_documents_infra(project_id=cfg.project_id, docs=docs)
    from shared_kernel import audit as _audit

    await _audit.emit(
        db,
        _audit.AuditEvent(
            action="rag.config_infra_purged",
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="rag_config",
            resource_id=config_id,
            metadata={"project_id": str(cfg.project_id), **outcome},
            request_id=ctx.request_id,
        ),
    )
    # The follow-up audit row is committed by the db_session dependency.


@config_router.get("/{config_id}/documents")
async def list_documents(
    config_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[RagDocumentOut]:
    service = RagConfigService(db)
    cfg = await service.get(config_id)
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope

    if not principal.is_admin:
        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(principal, Scope(project_id=cfg.project_id))
        if not roles:
            _raise_forbidden("caller is not a member of the config's project")
    from contexts.knowledge.infrastructure.repositories import (
        RagDocumentRepository,
    )

    docs = await RagDocumentRepository(db).list_for_config(config_id)
    return [_to_document_out(d) for d in docs]


@config_router.post(
    "/{config_id}/documents",
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    config_id: uuid.UUID = Path(...),
    file: UploadFile = File(...),
    mime: str | None = Form(default=None),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> RagDocumentOut:
    config_service = RagConfigService(db)
    cfg = await config_service.get(config_id)

    # Capability + R10.10 Owner check.
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=cfg.project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)
    await _require_owner(db=db, project_id=cfg.project_id, principal=principal)

    if cfg.embed_key_id is None:
        raise HTTPException(
            status_code=422,
            detail="rag config has no embed_key_id",
        )

    # API-4: enforce the 32 MB multipart cap BEFORE buffering the whole file
    # in memory — read up to cap + 1 byte and reject on overflow (the same
    # idiom as attachments.create_single_shot). IngestService re-checks the
    # cap, but only after the body would already be resident.
    data = await file.read(MAX_MULTIPART_BYTES + 1)
    if len(data) > MAX_MULTIPART_BYTES:
        raise DocumentTooLarge(
            f"multipart upload exceeds {MAX_MULTIPART_BYTES} bytes; use tus",
        )

    # Pinned-key embedder — the router unwraps + scrubs + accounts (R7.12);
    # the BYO key never enters this request handler.
    embedder = router_embedder_for(
        router=build_router(db),
        key_id=cfg.embed_key_id,
        provider=cfg.embed_provider,
        model=cfg.embed_model,
    )

    settings = get_settings()
    from minio import Minio

    minio = Minio(
        settings.minio.endpoint,
        access_key=settings.minio.root_access_key,
        secret_key=settings.minio.root_secret_key,
        secure=settings.minio.use_tls,
        region=settings.minio.region,
    )
    blob = MinioBlobStore(minio)

    qclient = AsyncQdrantClient(
        url=settings.qdrant.url,
        api_key=settings.qdrant.api_key or None,
    )
    qdrant = QdrantStore(qclient)

    ingest = IngestService(
        db,
        blob=blob,
        embedder=embedder,
        qdrant=qdrant,
        bucket=settings.minio.bucket_rag_sources,
    )
    doc = await ingest.ingest(
        ipt=IngestInput(
            rag_config_id=config_id,
            filename=file.filename or "upload",
            mime=mime or file.content_type or "application/octet-stream",
            data=data,
            uploaded_by=principal.user_id,
        ),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_document_out(doc)


document_router = APIRouter(prefix="/api/rag-documents", tags=["rag"])


@document_router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_rag_document(
    document_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    """§22.7 — hard-delete a RAG document.

    Removes the row + ``rag_chunks`` (FK cascade), then cleans up the Qdrant
    points and MinIO blob best-effort. Ordering matters (DOM-4): the DB row
    and audit record are written and committed *first* — the previous code
    deleted the Qdrant points and blob before the row/audit, so a rollback
    could leave an undeletable tombstone whose vectors+blob were already
    gone, with no audit row of the destructive action.

    AuthZ matches upload: ``RESOURCE_CREATE_EDIT`` at the parent config's
    project. We do NOT require Project Owner here — the R10.10 owner gate
    covers ingestion (write) only; deletion follows the standard edit
    capability so non-owner editors can clean up their own uploads.

    DOM-7: a missing document and an existing-but-forbidden document both
    return 404. Branching on existence before authorization (the old code
    returned 204 for any missing id) leaked a cross-tenant UUID enumeration
    oracle; deletion is therefore no longer idempotent for a vanished id.
    """
    from contexts.knowledge.infrastructure.repositories import (
        RagDocumentNotFound,
        RagDocumentRepository,
    )
    from shared_kernel import audit as _audit
    from shared_kernel.auth.dependencies import get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    docs_repo = RagDocumentRepository(db)
    try:
        doc = await docs_repo.require(document_id)
    except RagDocumentNotFound:
        # DOM-7: a missing id must not be distinguishable from an
        # existing-but-forbidden id, or the 204/403 split becomes a
        # cross-tenant document-UUID enumeration oracle. We cannot resolve a
        # project scope for a non-existent id, so report 404 — the very same
        # status an unauthorized caller receives below.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="rag document not found",
        ) from None

    config_service = RagConfigService(db)
    cfg = await config_service.get(doc.rag_config_id)

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=cfg.project_id),
        resolver,
    )
    if not decision.allowed:
        # DOM-7: return 404 (not 403) so an unauthorized caller cannot tell a
        # forbidden document apart from a missing one. Authorization is now
        # resolved before any existence-revealing branch.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="rag document not found",
        )

    # DOM-4: remove the DB row + write the audit record, then commit — that
    # commit is the point of no return. Only afterwards do the irreversible
    # infra deletes.
    await docs_repo.delete(document_id)
    await _audit.emit(
        db,
        _audit.AuditEvent(
            action="rag.document_deleted",
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="rag_document",
            resource_id=document_id,
            metadata={
                "rag_config_id": str(doc.rag_config_id),
                "project_id": str(cfg.project_id),
                "filename": doc.filename,
            },
            request_id=ctx.request_id,
        ),
    )
    await db.commit()

    # Best-effort purge of Qdrant points + MinIO blob, recorded in a
    # follow-up audit row (committed by the db_session dependency).
    outcome = await _purge_documents_infra(project_id=cfg.project_id, docs=[doc])
    await _audit.emit(
        db,
        _audit.AuditEvent(
            action="rag.document_infra_purged",
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="rag_document",
            resource_id=document_id,
            metadata={
                "rag_config_id": str(doc.rag_config_id),
                "project_id": str(cfg.project_id),
                **outcome,
            },
            request_id=ctx.request_id,
        ),
    )


__all__ = ["config_router", "document_router", "project_router"]
