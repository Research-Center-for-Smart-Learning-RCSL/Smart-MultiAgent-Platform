"""`/api/chatrooms/{id}/export` + `/api/exports/{job_id}` (F.10 / R13.17).

The POST enqueues an Arq `chat_export` job; the GET returns either a
`status: queued|running|failed` payload or a short-lived signed URL once
the worker has written the manifest to MinIO.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from arq.connections import RedisSettings, create_pool
from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.conversation.application import export_service
from contexts.conversation.application.access import (
    ensure_can_read,
    resolve_room_access,
)
from contexts.conversation.domain.errors import ExportJobNotFound
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    _raise_forbidden,
    current_context,
    current_principal,
)
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.observability.metrics import EXPORT_JOBS
from shared_kernel.storage import get_minio_client

chatroom_router = APIRouter(prefix="/api/chatrooms", tags=["exports"])
export_router = APIRouter(prefix="/api/exports", tags=["exports"])


class ExportCreateOut(BaseModel):
    job_id: uuid.UUID
    status: str


class ExportStatusOut(BaseModel):
    job_id: uuid.UUID
    chatroom_id: uuid.UUID
    status: str
    url: str | None
    error: str | None


@chatroom_router.post("/{chatroom_id}/export", status_code=202)
async def create_export(
    chatroom_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ExportCreateOut:
    access = await resolve_room_access(
        db, principal=principal, chatroom_id=chatroom_id,
    )
    ensure_can_read(access, is_admin=principal.is_admin)

    state = await export_service.create(
        chatroom_id=chatroom_id,
        owner_user_id=principal.user_id,
    )
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis.dsn))
    try:
        await pool.enqueue_job(
            "chat_export",
            str(state.job_id),
            str(chatroom_id),
            str(principal.user_id),
        )
    finally:
        await pool.close()
    EXPORT_JOBS.inc()
    _ = ctx
    return ExportCreateOut(job_id=state.job_id, status=state.status)


@export_router.get("/{job_id}")
async def get_export(
    job_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
) -> ExportStatusOut:
    state = await export_service.get(job_id)
    if state is None:
        raise ExportJobNotFound(str(job_id))
    if state.owner_user_id != principal.user_id and not principal.is_admin:
        _raise_forbidden("not the export owner")
    url: str | None = None
    if state.status == "ready" and state.bucket and state.object_key:
        url = await get_minio_client().presigned_get(
            bucket=state.bucket,
            key=state.object_key,
            expires=timedelta(minutes=15),
        )
    return ExportStatusOut(
        job_id=state.job_id,
        chatroom_id=state.chatroom_id,
        status=state.status,
        url=url,
        error=state.error,
    )


__all__ = ["chatroom_router", "export_router"]
