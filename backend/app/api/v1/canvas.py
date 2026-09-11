"""`/api/chatrooms/{chatroom_id}/canvas` -- Canvas CRUD endpoints ([R13.42]-[R13.50])."""

from __future__ import annotations

import os
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.domain.models import CanvasObjectKind
from contexts.canvas.interfaces.facade import CanvasFacade
from contexts.conversation.application.access import (
    ensure_can_read,
    ensure_can_send,
    is_room_creator,
    resolve_room_access,
)
from contexts.conversation.infrastructure.channels import room_channel
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.auth.ratelimit import check_raw as rate_check_raw
from shared_kernel.db.session import db_session
from shared_kernel.storage import get_minio_client
from shared_kernel.storage.svg_sanitize import SvgSanitizeError, sanitize_svg

router = APIRouter(prefix="/api/chatrooms/{chatroom_id}/canvas", tags=["canvas"])

_CANVAS_IMAGE_MAX_BYTES = int(os.environ.get("CANVAS_IMAGE_MAX_BYTES", str(10 * 1024 * 1024)))
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
    created_by_agent_id: uuid.UUID | None
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
            created_by_agent_id=o.created_by_agent_id,
            created_at=o.created_at.isoformat(),
            updated_at=o.updated_at.isoformat(),
            image_url=image_url,
        )


class BatchUpdateItem(BaseModel):
    id: uuid.UUID
    position_x: float | None = None
    position_y: float | None = None
    width: float | None = None
    height: float | None = None
    z_index: int | None = None
    content: str | None = None
    style: dict[str, Any] | None = None


class BatchOpIn(BaseModel):
    creates: list[CanvasObjectIn] | None = None
    updates: list[BatchUpdateItem] | None = None
    deletes: list[uuid.UUID] | None = None


class SnapshotCreateIn(BaseModel):
    label: str | None = Field(None, max_length=200)


class SnapshotOut(BaseModel):
    id: uuid.UUID
    canvas_id: uuid.UUID
    agent_digest: str | None
    created_by_user_id: uuid.UUID | None
    label: str | None
    created_at: str

    @classmethod
    def from_domain(cls, s: Any) -> SnapshotOut:
        return cls(
            id=s.id,
            canvas_id=s.canvas_id,
            agent_digest=s.agent_digest,
            created_by_user_id=s.created_by_user_id,
            label=s.label,
            created_at=s.created_at.isoformat(),
        )


class SnapshotDetailOut(SnapshotOut):
    snapshot_data: dict[str, Any]

    @classmethod
    def from_domain(cls, s: Any) -> SnapshotDetailOut:
        return cls(
            id=s.id,
            canvas_id=s.canvas_id,
            agent_digest=s.agent_digest,
            created_by_user_id=s.created_by_user_id,
            label=s.label,
            snapshot_data=s.snapshot_data,
            created_at=s.created_at.isoformat(),
        )


class CanvasSearchResult(BaseModel):
    object_id: uuid.UUID
    kind: CanvasObjectKind
    snippet: str
    rank: float
    position_x: float
    position_y: float


class CommentIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class CommentOut(BaseModel):
    id: uuid.UUID
    object_id: uuid.UUID
    content: str
    created_by_user_id: uuid.UUID | None
    created_by_guest_id: uuid.UUID | None
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(cls, c: Any) -> CommentOut:
        return cls(
            id=c.id,
            object_id=c.object_id,
            content=c.content,
            created_by_user_id=c.created_by_user_id,
            created_by_guest_id=c.created_by_guest_id,
            created_at=c.created_at.isoformat(),
            updated_at=c.updated_at.isoformat(),
        )


# ---- Helpers ----------------------------------------------------------------


def _actor_user_id(principal: Principal) -> uuid.UUID | None:
    return principal.user_id if not principal.is_guest else None


def _actor_guest_id(principal: Principal) -> uuid.UUID | None:
    return principal.user_id if principal.is_guest else None


async def _enforce_guest_rate_limit(principal: Principal) -> None:
    """Rate-limit guest canvas mutations (AC-11, R13.46)."""
    if not principal.is_guest:
        return
    guest_id = str(principal.user_id)
    decision = await rate_check_raw(
        key=f"rl:canvas-guest:{guest_id}",
        window_sec=60,
        max_count=_GUEST_RATE_LIMIT_PER_MINUTE,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Canvas rate limit exceeded",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )


def _is_comment_author(comment: Any, principal: Principal) -> bool:
    if principal.is_guest:
        return bool(comment.created_by_guest_id == principal.user_id)
    return bool(comment.created_by_user_id == principal.user_id)


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _sanitize_label(label: str) -> str:
    cleaned = _CONTROL_CHARS_RE.sub("", label)
    cleaned = " ".join(cleaned.split())
    return cleaned[:200]


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
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_by_chatroom(chatroom_id)
    if canvas is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canvas not found")
    return CanvasOut.from_domain(canvas)


@router.get("/search")
async def search_canvas(
    chatroom_id: uuid.UUID,
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(default=50, ge=1, le=50),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[CanvasSearchResult]:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_by_chatroom(chatroom_id)
    if canvas is None:
        return []
    results = await facade.search_objects(canvas.id, q, limit=limit)
    return [
        CanvasSearchResult(
            object_id=obj.id,
            kind=obj.kind,
            snippet=snippet,
            rank=rank,
            position_x=obj.position_x,
            position_y=obj.position_y,
        )
        for obj, rank, snippet in results
    ]


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
    await _enforce_guest_rate_limit(principal)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    updated = await facade.update_settings(
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        expose_to_agents=body.expose_to_agents,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.actor_ip,
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
    await _enforce_guest_rate_limit(principal)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    await facade.delete(
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.actor_ip,
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
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_by_chatroom(chatroom_id)
    if canvas is None:
        return []
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
    await _enforce_guest_rate_limit(principal)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
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
        actor_ip=ctx.actor_ip,
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
    await _enforce_guest_rate_limit(principal)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
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
        actor_ip=ctx.actor_ip,
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
    await _enforce_guest_rate_limit(principal)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    deleted = await facade.delete_object(
        object_id=object_id,
        chatroom_id=chatroom_id,
        canvas_id=canvas.id,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.actor_ip,
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
    await _enforce_guest_rate_limit(principal)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    creates = [c.model_dump() for c in body.creates] if body.creates else None
    updates = (
        [u.model_dump(exclude_unset=True) | {"id": str(u.id)} for u in body.updates] if body.updates else None
    )
    result = await facade.batch_operate(
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        creates=creates,
        updates=updates,
        deletes=body.deletes,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.actor_ip,
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
    await _enforce_guest_rate_limit(principal)

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

    if file.content_type == "image/svg+xml":
        try:
            content = sanitize_svg(content)
        except SvgSanitizeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid SVG: {exc}",
            ) from exc

    facade = CanvasFacade(db, room_channel_fn=room_channel)
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

    minio = get_minio_client()
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
        actor_ip=ctx.actor_ip,
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
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_by_chatroom(chatroom_id)
    if canvas is None:
        return []
    snapshots = await facade.list_snapshots(canvas.id, limit=min(limit, 50), offset=offset)
    return [SnapshotOut.from_domain(s) for s in snapshots]


@router.get("/snapshots/{snapshot_id}")
async def get_snapshot(
    chatroom_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SnapshotDetailOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_by_chatroom(chatroom_id)
    if canvas is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    snap = await facade.get_snapshot(canvas.id, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return SnapshotDetailOut.from_domain(snap)


@router.post("/snapshots", status_code=status.HTTP_201_CREATED)
async def create_snapshot(
    chatroom_id: uuid.UUID,
    body: SnapshotCreateIn | None = None,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> SnapshotOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    await _enforce_guest_rate_limit(principal)
    label = _sanitize_label(body.label) if body and body.label else None
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    snap = await facade.create_snapshot(
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        label=label,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    return SnapshotOut.from_domain(snap)


@router.post("/snapshots/{snapshot_id}/restore")
async def restore_snapshot(
    chatroom_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> SnapshotOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    await _enforce_guest_rate_limit(principal)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_by_chatroom(chatroom_id)
    if canvas is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    auto_save = await facade.restore_snapshot(
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        snapshot_id=snapshot_id,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    if auto_save is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await db.commit()
    return SnapshotOut.from_domain(auto_save)


# ---- Comments ---------------------------------------------------------------


@router.get("/objects/comment-counts")
async def get_comment_counts(
    chatroom_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> dict[str, int]:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_by_chatroom(chatroom_id)
    if canvas is None:
        return {}
    objects = await facade.list_objects(canvas.id)
    object_ids = [o.id for o in objects]
    if not object_ids:
        return {}
    counts = await facade.count_comments_by_object(object_ids, canvas_id=canvas.id)
    return {str(oid): cnt for oid, cnt in counts.items()}


@router.get("/objects/{object_id}/comments")
async def list_comments(
    chatroom_id: uuid.UUID,
    object_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[CommentOut]:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_by_chatroom(chatroom_id)
    if canvas is None:
        return []
    comments = await facade.list_comments(
        object_id, canvas_id=canvas.id, limit=min(limit, 100), offset=offset
    )
    return [CommentOut.from_domain(c) for c in comments]


@router.post("/objects/{object_id}/comments", status_code=status.HTTP_201_CREATED)
async def create_comment(
    chatroom_id: uuid.UUID,
    object_id: uuid.UUID,
    body: CommentIn,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> CommentOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    await _enforce_guest_rate_limit(principal)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    objects = await facade.list_objects(canvas.id)
    if not any(o.id == object_id for o in objects):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Object not found on this canvas")
    comment = await facade.create_comment(
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        object_id=object_id,
        content=body.content,
        created_by_user_id=_actor_user_id(principal),
        created_by_guest_id=_actor_guest_id(principal),
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    return CommentOut.from_domain(comment)


@router.patch("/comments/{comment_id}")
async def update_comment(
    chatroom_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: CommentIn,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> CommentOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    await _enforce_guest_rate_limit(principal)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_by_chatroom(chatroom_id)
    if canvas is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    comment = await facade.get_comment(comment_id, canvas_id=canvas.id)
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not _is_comment_author(comment, principal) and not principal.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the comment author")
    updated = await facade.update_comment(
        comment_id=comment_id,
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        content=body.content,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return CommentOut.from_domain(updated)


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    chatroom_id: uuid.UUID,
    comment_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> None:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    await _enforce_guest_rate_limit(principal)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_by_chatroom(chatroom_id)
    if canvas is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    comment = await facade.get_comment(comment_id, canvas_id=canvas.id)
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    is_author = _is_comment_author(comment, principal)
    is_creator_or_admin = principal.is_admin or is_room_creator(access, principal=principal)
    if not is_author and not is_creator_or_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete this comment")
    deleted = await facade.delete_comment(
        comment_id=comment_id,
        canvas_id=canvas.id,
        chatroom_id=chatroom_id,
        object_id=comment.object_id,
        actor_user_id=_actor_user_id(principal),
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
