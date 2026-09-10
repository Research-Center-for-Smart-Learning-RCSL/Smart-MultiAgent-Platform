"""`/api/chatrooms/{chatroom_id}/canvas` -- Canvas CRUD endpoints ([R13.33]-[R13.41])."""

from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.domain.models import CanvasObjectKind
from contexts.canvas.interfaces.facade import CanvasFacade
from contexts.conversation.application.access import (
    ensure_can_read,
    ensure_can_send,
    resolve_room_access,
)
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.storage.minio_client import MinioClient

router = APIRouter(prefix="/api/chatrooms/{chatroom_id}/canvas", tags=["canvas"])

_CANVAS_IMAGE_MAX_BYTES = int(os.environ.get("CANVAS_IMAGE_MAX_BYTES", 10 * 1024 * 1024))
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
_GUEST_RATE_LIMIT_PER_MINUTE = 60


# ---- Request/Response models -----------------------------------------------


class CanvasOut(BaseModel):
    id: uuid.UUID
    chatroom_id: uuid.UUID
    expose_to_agents: bool
    created_at: str

    @classmethod
    def from_domain(cls, c: Any) -> CanvasOut:
        return cls(
            id=c.id,
            chatroom_id=c.chatroom_id,
            expose_to_agents=c.expose_to_agents,
            created_at=c.created_at.isoformat(),
        )


class CanvasSettingsIn(BaseModel):
    expose_to_agents: bool


class CanvasObjectIn(BaseModel):
    kind: CanvasObjectKind
    position_x: float
    position_y: float
    width: float
    height: float
    z_index: int = 0
    content: str | None = None
    style: dict[str, Any] | None = None


class CanvasObjectPatch(BaseModel):
    position_x: float | None = None
    position_y: float | None = None
    width: float | None = None
    height: float | None = None
    z_index: int | None = None
    content: str | None = None
    style: dict[str, Any] | None = None


class CanvasObjectOut(BaseModel):
    id: uuid.UUID
    canvas_id: uuid.UUID
    kind: CanvasObjectKind
    content: str | None
    minio_path: str | None
    position_x: float
    position_y: float
    width: float
    height: float
    z_index: int
    style: dict[str, Any]
    created_by_user_id: uuid.UUID | None
    created_by_guest_id: uuid.UUID | None
    created_at: str
    updated_at: str
    image_url: str | None = None

    @classmethod
    def from_domain(cls, o: Any, *, image_url: str | None = None) -> CanvasObjectOut:
        return cls(
            id=o.id,
            canvas_id=o.canvas_id,
            kind=o.kind,
            content=o.content,
            minio_path=o.minio_path,
            position_x=o.position_x,
            position_y=o.position_y,
            width=o.width,
            height=o.height,
            z_index=o.z_index,
            style=o.style,
            created_by_user_id=o.created_by_user_id,
            created_by_guest_id=o.created_by_guest_id,
            created_at=o.created_at.isoformat(),
            updated_at=o.updated_at.isoformat(),
            image_url=image_url,
        )


class BatchOpIn(BaseModel):
    creates: list[CanvasObjectIn] | None = None
    updates: list[dict[str, Any]] | None = None
    deletes: list[uuid.UUID] | None = None


class SnapshotOut(BaseModel):
    id: uuid.UUID
    canvas_id: uuid.UUID
    agent_digest: str | None
    created_by_user_id: uuid.UUID | None
    created_at: str

    @classmethod
    def from_domain(cls, s: Any) -> SnapshotOut:
        return cls(
            id=s.id,
            canvas_id=s.canvas_id,
            agent_digest=s.agent_digest,
            created_by_user_id=s.created_by_user_id,
            created_at=s.created_at.isoformat(),
        )


# ---- Helpers ----------------------------------------------------------------


def _actor_user_id(principal: Principal) -> uuid.UUID | None:
    return principal.user_id if not principal.is_guest else None


def _actor_guest_id(principal: Principal) -> uuid.UUID | None:
    return principal.guest_session_id if principal.is_guest else None


def _canvas_image_key(
    *,
    project_id: uuid.UUID,
    canvas_id: uuid.UUID,
    object_id: uuid.UUID,
    filename: str,
) -> str:
    safe_name = filename.replace("/", "_").replace("\\", "_")
    return f"canvas-images/{project_id}/{canvas_id}/{object_id}/{safe_name}"


# ---- Canvas CRUD ------------------------------------------------------------


@router.get("")
async def get_canvas(
    chatroom_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> CanvasOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db)
    canvas = await facade.get_or_create(
        chatroom_id=chatroom_id,
        actor_user_id=_actor_user_id(principal),
    )
    await db.commit()
    return CanvasOut.from_domain(canvas)


@router.patch("")
async def update_canvas_settings(
    chatroom_id: uuid.UUID,
    body: CanvasSettingsIn,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> CanvasOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    updated = await facade.update_settings(
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        expose_to_agents=body.expose_to_agents,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.client_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return CanvasOut.from_domain(updated)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_canvas(
    chatroom_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> None:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    await facade.delete(
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.client_ip,
        request_id=ctx.request_id,
    )
    await db.commit()


# ---- Objects ----------------------------------------------------------------


@router.get("/objects")
async def list_objects(
    chatroom_id: uuid.UUID,
    limit: int = 500,
    offset: int = 0,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[CanvasObjectOut]:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    await db.commit()
    objects = await facade.list_objects(canvas.id, limit=min(limit, 500), offset=offset)
    return [CanvasObjectOut.from_domain(o) for o in objects]


@router.post("/objects", status_code=status.HTTP_201_CREATED)
async def create_object(
    chatroom_id: uuid.UUID,
    body: CanvasObjectIn,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> CanvasObjectOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    obj = await facade.create_object(
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        kind=body.kind,
        position_x=body.position_x,
        position_y=body.position_y,
        width=body.width,
        height=body.height,
        z_index=body.z_index,
        content=body.content,
        style=body.style,
        created_by_user_id=_actor_user_id(principal),
        created_by_guest_id=_actor_guest_id(principal),
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.client_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    return CanvasObjectOut.from_domain(obj)


@router.patch("/objects/{object_id}")
async def update_object(
    chatroom_id: uuid.UUID,
    object_id: uuid.UUID,
    body: CanvasObjectPatch,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> CanvasObjectOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    values = body.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")
    obj = await facade.update_object(
        object_id=object_id,
        chatroom_id=chatroom_id,
        canvas_id=canvas.id,
        values=values,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.client_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return CanvasObjectOut.from_domain(obj)


@router.delete("/objects/{object_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_object(
    chatroom_id: uuid.UUID,
    object_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> None:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    deleted = await facade.delete_object(
        object_id=object_id,
        chatroom_id=chatroom_id,
        canvas_id=canvas.id,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.client_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


@router.post("/objects/batch")
async def batch_operate(
    chatroom_id: uuid.UUID,
    body: BatchOpIn,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> dict[str, Any]:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    creates = [c.model_dump() for c in body.creates] if body.creates else None
    result = await facade.batch_operate(
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        creates=creates,
        updates=body.updates,
        deletes=body.deletes,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.client_ip,
        actor_guest_id=_actor_guest_id(principal),
        request_id=ctx.request_id,
    )
    await db.commit()
    created_out = [CanvasObjectOut.from_domain(o) for o in result.get("created", [])]
    return {"created": created_out, "updated": result["updated"], "deleted": result["deleted"]}


# ---- Images -----------------------------------------------------------------


@router.post("/images", status_code=status.HTTP_201_CREATED)
async def upload_image(
    chatroom_id: uuid.UUID,
    file: UploadFile,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> CanvasObjectOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)

    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported image type: {file.content_type}",
        )

    content = await file.read()
    if len(content) > _CANVAS_IMAGE_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Image too large (max {_CANVAS_IMAGE_MAX_BYTES // (1024 * 1024)} MB)",
        )

    facade = CanvasFacade(db)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)

    image_count = await facade.count_images(canvas.id)
    if image_count >= facade.max_images_per_canvas():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Canvas image limit reached ({facade.max_images_per_canvas()})",
        )

    object_id = uuid.uuid4()
    filename = file.filename or "image"
    key = _canvas_image_key(
        project_id=access.project_id,
        canvas_id=canvas.id,
        object_id=object_id,
        filename=filename,
    )

    minio = MinioClient()
    await minio.put_object(
        bucket=minio.chat_uploads_bucket,
        key=key,
        data=content,
        content_type=file.content_type or "application/octet-stream",
    )

    obj = await facade.create_object(
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        kind=CanvasObjectKind.IMAGE,
        position_x=0,
        position_y=0,
        width=400,
        height=300,
        minio_path=key,
        created_by_user_id=_actor_user_id(principal),
        created_by_guest_id=_actor_guest_id(principal),
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.client_ip,
        request_id=ctx.request_id,
    )
    await db.commit()

    image_url = await minio.presigned_get(bucket=minio.chat_uploads_bucket, key=key)
    return CanvasObjectOut.from_domain(obj, image_url=image_url)


# ---- Snapshots --------------------------------------------------------------


@router.get("/snapshots")
async def list_snapshots(
    chatroom_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[SnapshotOut]:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    await db.commit()
    snapshots = await facade.list_snapshots(canvas.id, limit=min(limit, 50), offset=offset)
    return [SnapshotOut.from_domain(s) for s in snapshots]


@router.post("/snapshots", status_code=status.HTTP_201_CREATED)
async def create_snapshot(
    chatroom_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> SnapshotOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    snap = await facade.create_snapshot(
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.client_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    return SnapshotOut.from_domain(snap)
