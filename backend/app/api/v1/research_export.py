"""`/api/workspaces/{id}/export/research-data` + `/api/exports/research/{job_id}` (R33.10).

The POST enqueues an Arq ``research_data_export`` job; the GET returns either
a ``status: queued|running|failed`` payload or a short-lived presigned URL
once the worker has written the ZIP to MinIO.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.application import research_export_service
from contexts.conversation.application.research_export_service import ResearchExportJobStatus
from contexts.conversation.domain.errors import ExportJobNotFound
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    _raise_forbidden,
    current_context,
    current_principal,
)
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.storage import get_minio_client

workspace_router = APIRouter(prefix="/api/workspaces", tags=["research-export"])
export_router = APIRouter(prefix="/api/exports/research", tags=["research-export"])


class ResearchExportCreateIn(BaseModel):
    created_after: date | None = None
    created_before: date | None = None

    @model_validator(mode="after")
    def _validate_range(self) -> ResearchExportCreateIn:
        if (
            self.created_after is not None
            and self.created_before is not None
            and self.created_before < self.created_after
        ):
            raise ValueError("created_before must not precede created_after")
        return self


def _resolve_window(body: ResearchExportCreateIn) -> tuple[datetime | None, datetime | None]:
    after = datetime.combine(body.created_after, time.min, tzinfo=UTC) if body.created_after else None
    before = (
        datetime.combine(body.created_before + timedelta(days=1), time.min, tzinfo=UTC)
        if body.created_before
        else None
    )
    return after, before


class ResearchExportCreateOut(BaseModel):
    job_id: uuid.UUID
    status: str


class ResearchExportStatusOut(BaseModel):
    job_id: uuid.UUID
    workspace_id: uuid.UUID
    status: str
    url: str | None
    error: str | None


_PRESIGNED_URL_EXPIRY = timedelta(hours=72)


@workspace_router.post("/{workspace_id}/export/research-data", status_code=202)
async def create_research_export(
    workspace_id: uuid.UUID = Path(...),
    body: ResearchExportCreateIn | None = None,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ResearchExportCreateOut:
    from app.api.v1.deps import assert_project_owner
    from contexts.conversation.interfaces.facade import ConversationFacade

    facade = ConversationFacade(db)
    workspace = await facade.get_workspace(workspace_id)
    if workspace is None or workspace.deleted_at is not None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Workspace not found")

    await assert_project_owner(db=db, principal=principal, project_id=workspace.project_id)

    body = body or ResearchExportCreateIn()
    created_after, created_before = _resolve_window(body)

    state = await research_export_service.create(
        workspace_id=workspace_id,
        owner_user_id=principal.user_id,
        created_after=created_after,
        created_before=created_before,
        actor_ip=ctx.actor_ip,
    )

    from shared_kernel.queue import enqueue

    await enqueue(
        "research_data_export",
        str(state.job_id),
        str(workspace_id),
        str(principal.user_id),
    )
    return ResearchExportCreateOut(job_id=state.job_id, status=state.status)


@export_router.get("/{job_id}")
async def get_research_export(
    job_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
) -> ResearchExportStatusOut:
    state = await research_export_service.get(job_id)
    if state is None:
        raise ExportJobNotFound(str(job_id))
    if state.owner_user_id != principal.user_id and not principal.is_admin:
        _raise_forbidden("not the export owner")

    url: str | None = None
    if state.status == ResearchExportJobStatus.READY and state.bucket and state.object_key:
        url = await get_minio_client().presigned_get(
            bucket=state.bucket,
            key=state.object_key,
            expires=_PRESIGNED_URL_EXPIRY,
        )
    return ResearchExportStatusOut(
        job_id=state.job_id,
        workspace_id=state.workspace_id,
        status=state.status,
        url=url,
        error=state.error,
    )


__all__ = ["export_router", "workspace_router"]
