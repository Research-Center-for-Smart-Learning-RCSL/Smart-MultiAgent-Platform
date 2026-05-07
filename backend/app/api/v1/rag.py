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
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.keys.interfaces.facade import KeysFacade
from contexts.knowledge.application.config_service import RagConfigService
from contexts.knowledge.application.ingest_service import IngestInput, IngestService
from contexts.knowledge.domain.models import ChunkStrategy, RagConfigDraft
from contexts.knowledge.infrastructure.blob_store import MinioBlobStore
from contexts.knowledge.infrastructure.embedders import embedder_for
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
    await service.soft_delete(
        config_id=config_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


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

    data = await file.read()

    # Unwrap the BYO embedding key just long enough to sign the batch.
    plaintext = await KeysFacade(db).unwrap_api_key_plaintext(cfg.embed_key_id)
    try:
        embedder = embedder_for(
            provider=cfg.embed_provider,
            model=cfg.embed_model,
            api_key=plaintext.decode("utf-8"),
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
    finally:
        # Best-effort scrub of the plaintext key.
        plaintext = b"\x00" * len(plaintext)
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

    Cleans up Qdrant points and the MinIO blob best-effort. The chunk rows
    are removed by the FK cascade on ``rag_chunks.document_id``.

    AuthZ matches upload: ``RESOURCE_CREATE_EDIT`` at the parent config's
    project. We do NOT require Project Owner here — the R10.10 owner gate
    covers ingestion (write) only; deletion follows the standard edit
    capability so non-owner editors can clean up their own uploads.
    """
    from contexts.knowledge.infrastructure.repositories import (
        RagDocumentNotFound,
        RagDocumentRepository,
    )
    from shared_kernel import audit as _audit
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    docs_repo = RagDocumentRepository(db)
    try:
        doc = await docs_repo.require(document_id)
    except RagDocumentNotFound:
        # Idempotent: a 204 on a missing id is preferable to a 404 race
        # against concurrent deletes; the audit trail records the attempt.
        return

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
        _raise_forbidden(decision.reason)

    # Best-effort cleanup of Qdrant points first — if that fails we still
    # delete the row so the user isn't stuck with a tombstone.
    try:
        settings = get_settings()
        qclient = AsyncQdrantClient(
            url=settings.qdrant.url,
            api_key=settings.qdrant.api_key or None,
        )
        try:
            await QdrantStore(qclient).delete_document(
                project_id=cfg.project_id,
                document_id=document_id,
            )
        finally:
            await qclient.close()
    except Exception:  # noqa: S110 — best-effort cleanup; audit metadata records the outcome
        pass

    # Best-effort MinIO blob removal.
    blob_removed = True
    try:
        from minio import Minio

        settings = get_settings()
        minio = Minio(
            settings.minio.endpoint,
            access_key=settings.minio.root_access_key,
            secret_key=settings.minio.root_secret_key,
            secure=settings.minio.use_tls,
            region=settings.minio.region,
        )
        # minio_path is "<bucket>/<key>"; split on first slash.
        bucket, _, key = doc.minio_path.partition("/")
        if bucket and key:
            import asyncio

            await asyncio.to_thread(minio.remove_object, bucket, key)
    except Exception:
        blob_removed = False

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
                "blob_removed": blob_removed,
            },
            request_id=ctx.request_id,
        ),
    )


__all__ = ["config_router", "document_router", "project_router"]
