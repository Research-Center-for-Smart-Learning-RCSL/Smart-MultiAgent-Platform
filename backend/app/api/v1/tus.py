"""`/api/tus` — TUS v1.0.0 resumable upload protocol (R22.15).

Only the subset of headers SMAP needs is implemented; Expiration / Checksum
extensions are not advertised. Creation + HEAD + PATCH + DELETE cover the
client flow (tus-js-client "creation,termination" extensions).

ACL: the Creation POST is the ONLY point the caller proves authorisation.
The `upload_id` returned in Location is a capability token — the TUS
service compares it to `user_id` on every subsequent HEAD/PATCH.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.application.access import (
    ensure_can_send,
    resolve_room_access,
)
from contexts.conversation.application.tus_service import (
    TUS_EXTENSIONS,
    TUS_MAX_BYTES,
    TUS_MAX_CHUNK,
    TUS_VERSION,
    TusService,
)
from contexts.conversation.domain.errors import (
    AttachmentTooLarge,
    TusMetadataInvalid,
)
from contexts.conversation.infrastructure.tus_store import parse_metadata
from contexts.knowledge.interfaces.facade import KnowledgeFacade
from contexts.tenancy.domain.models import ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import ProjectMemberRepository
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/tus", tags=["tus"])


def _tus_base_headers() -> dict[str, str]:
    # These headers must be present on every TUS response.
    return {"Tus-Resumable": TUS_VERSION}


async def _require_rag_owner(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    principal: Principal,
) -> None:
    """R10.10 — only a Project Owner (or platform Admin) may upload RAG
    sources. Mirrors `rag.py::_require_owner` for the tus path."""
    if principal.is_admin:
        return
    member = await ProjectMemberRepository(db).get(project_id=project_id, user_id=principal.user_id)
    if member is None or member.role is not ProjectMemberRole.OWNER:
        raise HTTPException(
            status_code=403,
            detail="R10.10 requires Project Owner to upload RAG sources",
        )


@router.options("")
async def tus_options() -> Response:
    headers = {
        "Tus-Resumable": TUS_VERSION,
        "Tus-Version": TUS_VERSION,
        "Tus-Extension": TUS_EXTENSIONS,
        "Tus-Max-Size": str(TUS_MAX_BYTES),
    }
    return Response(status_code=204, headers=headers)


@router.post("", status_code=201)
async def tus_create(
    tus_resumable: str = Header(..., alias="Tus-Resumable"),
    upload_length: int = Header(..., alias="Upload-Length"),
    upload_metadata: str = Header(default="", alias="Upload-Metadata"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> Response:
    _require_version(tus_resumable)
    # Enforce ACL for chat_attachment creation BEFORE allocating state: we
    # need to know the caller can send into the declared chatroom.
    try:
        meta = parse_metadata(upload_metadata)
    except ValueError as exc:
        raise TusMetadataInvalid(str(exc)) from exc
    purpose = meta.get("purpose")
    if purpose == "chat_attachment":
        try:
            chatroom_id = uuid.UUID(meta.get("chatroom_id", ""))
        except ValueError as exc:
            raise TusMetadataInvalid("chatroom_id must be UUID") from exc
        access = await resolve_room_access(
            db,
            principal=principal,
            chatroom_id=chatroom_id,
        )
        ensure_can_send(access, is_admin=principal.is_admin)
    elif purpose == "rag_source":
        # Authorise against the config's REAL project (not the client-declared
        # project_id in metadata): resolve the rag_config, then require the
        # caller be a Project Owner — the same R10.10 gate the multipart RAG
        # upload enforces. The finaliser likewise keys the blob off the config's
        # project, so a forged metadata project_id buys nothing.
        try:
            rag_config_id = uuid.UUID(meta.get("rag_config_id", ""))
        except ValueError as exc:
            raise TusMetadataInvalid("rag_config_id must be UUID") from exc
        cfg = await KnowledgeFacade(db).get_rag_config(rag_config_id)
        if cfg is None:
            raise HTTPException(status_code=404, detail="rag config not found")
        await _require_rag_owner(db, project_id=cfg.project_id, principal=principal)
    else:
        # Any other/unset purpose has no ACL-gated finaliser — fail closed.
        raise HTTPException(
            status_code=403,
            detail=f"TUS upload purpose {purpose!r} is not enabled",
        )

    service = TusService(db)
    result = await service.create(
        user_id=principal.user_id,
        upload_length=upload_length,
        metadata_raw=upload_metadata,
    )
    _ = ctx
    headers = _tus_base_headers() | {
        "Location": result.location,
        "Upload-Offset": "0",
    }
    return Response(status_code=201, headers=headers)


@router.head("/{upload_id}")
async def tus_head(
    upload_id: uuid.UUID = Path(...),
    tus_resumable: str = Header(..., alias="Tus-Resumable"),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> Response:
    _require_version(tus_resumable)
    service = TusService(db)
    info = await service.head(upload_id=upload_id, user_id=principal.user_id)
    headers = _tus_base_headers() | {
        "Upload-Length": str(info.upload_length),
        "Upload-Offset": str(info.upload_offset),
        "Cache-Control": "no-store",
    }
    if info.metadata_raw:
        headers["Upload-Metadata"] = info.metadata_raw
    return Response(status_code=200, headers=headers)


@router.patch("/{upload_id}")
async def tus_patch(
    request: Request,
    upload_id: uuid.UUID = Path(...),
    tus_resumable: str = Header(..., alias="Tus-Resumable"),
    upload_offset: int = Header(..., alias="Upload-Offset"),
    content_type: str = Header(..., alias="Content-Type"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> Response:
    _require_version(tus_resumable)
    if content_type != "application/offset+octet-stream":
        raise HTTPException(
            status_code=415,
            detail="Content-Type must be application/offset+octet-stream",
        )
    # API-4: reject oversized chunks BEFORE buffering the body. request.body()
    # pulls the entire chunk into RAM, so without the Content-Length pre-check
    # a client could force the allocation and only then hit the length check.
    declared = request.headers.get("Content-Length")
    if declared is not None:
        try:
            declared_len = int(declared)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="invalid Content-Length header",
            ) from None
        if declared_len > TUS_MAX_CHUNK:
            raise AttachmentTooLarge(
                f"PATCH chunk Content-Length {declared_len} bytes exceeds 16 MB cap",
            )
    body = await request.body()
    if len(body) > TUS_MAX_CHUNK:
        raise AttachmentTooLarge(
            f"PATCH chunk {len(body)} bytes exceeds 16 MB cap",
        )

    service = TusService(db)
    result = await service.patch(
        upload_id=upload_id,
        user_id=principal.user_id,
        offset=upload_offset,
        chunk=body,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    headers = _tus_base_headers() | {"Upload-Offset": str(result.new_offset)}
    # X-SMAP-Resource lets the client jump straight to the created resource
    # once the upload completes (R22.15.05).
    if result.completed and result.attachment is not None:
        headers["X-SMAP-Resource"] = f"/api/attachments/{result.attachment.id}"
    elif result.completed and result.rag_document_id is not None:
        headers["X-SMAP-Resource"] = f"/api/rag-documents/{result.rag_document_id}"
    return Response(status_code=204, headers=headers)


@router.delete("/{upload_id}", status_code=204)
async def tus_terminate(
    upload_id: uuid.UUID = Path(...),
    tus_resumable: str = Header(..., alias="Tus-Resumable"),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> Response:
    _require_version(tus_resumable)
    service = TusService(db)
    await service.terminate(upload_id=upload_id, user_id=principal.user_id)
    return Response(status_code=204, headers=_tus_base_headers())


def _require_version(header: str) -> None:
    if header != TUS_VERSION:
        raise HTTPException(
            status_code=412,
            detail=f"Tus-Resumable must be {TUS_VERSION}",
        )


__all__ = ["router"]
