"""`/api/workspaces/{id}/export/research-data` + `/api/exports/research/...` (R33.10).

The POST enqueues an Arq ``research_data_export`` job, or answers 409 with the
owner's running job for the workspace. ``GET .../latest`` names the owner's
latest job so the dashboard can re-open it. ``GET .../{job_id}`` returns either
a ``status: queued|running|failed`` payload or, once the ZIP is in MinIO, a
short-lived ``/api/files/{ticket}`` link minted per request; a ready record
whose archive is gone answers 410.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import assert_computed_project_owner
from contexts.conversation.application import research_export_service
from contexts.conversation.application.research_export_access import still_entitled
from contexts.conversation.application.research_export_service import ResearchExportJobStatus
from contexts.conversation.domain.errors import ExportJobNotFound, ResearchExportExpired
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    _raise_forbidden,
    current_context,
    current_principal,
)
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.storage import file_tickets, get_minio_client
from shared_kernel.storage.file_tickets import content_disposition

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


class ResearchExportLatestOut(BaseModel):
    job_id: uuid.UUID
    status: str


async def _assert_workspace_owner(db: AsyncSession, principal: Principal, workspace_id: uuid.UUID) -> None:
    from contexts.conversation.interfaces.facade import ConversationFacade

    workspace = await ConversationFacade(db).get_workspace(workspace_id)
    if workspace is None or workspace.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    await assert_computed_project_owner(db=db, principal=principal, project_id=workspace.project_id)


@workspace_router.post("/{workspace_id}/export/research-data", status_code=202)
async def create_research_export(
    workspace_id: uuid.UUID = Path(...),
    body: ResearchExportCreateIn | None = None,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ResearchExportCreateOut:
    await _assert_workspace_owner(db, principal, workspace_id)

    body = body or ResearchExportCreateIn()
    created_after, created_before = _resolve_window(body)

    # Raises ResearchExportInProgress (409, with the running job's id) while one is active.
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


# Declared before `/{job_id}` so "latest" is not parsed as a job id.
@export_router.get("/latest")
async def get_latest_research_export(
    workspace_id: uuid.UUID = Query(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ResearchExportLatestOut:
    await _assert_workspace_owner(db, principal, workspace_id)
    state = await research_export_service.latest(owner_user_id=principal.user_id, workspace_id=workspace_id)
    if state is None:
        raise ExportJobNotFound(str(workspace_id))
    return ResearchExportLatestOut(job_id=state.job_id, status=state.status)


@export_router.get("/{job_id}")
async def get_research_export(
    job_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ResearchExportStatusOut:
    state = await research_export_service.get(job_id)
    if state is None:
        raise ExportJobNotFound(str(job_id))
    if state.owner_user_id != principal.user_id and not principal.is_admin:
        _raise_forbidden("not the export owner")
    # Ownership at request time is not enough: a requester demoted since then
    # must not keep minting links (audit 2026-10-05 F-13).
    if not await still_entitled(db, workspace_id=state.workspace_id, user_id=principal.user_id):
        _raise_forbidden("no longer entitled to this workspace's research export")

    url: str | None = None
    if state.status == ResearchExportJobStatus.READY and state.bucket and state.object_key:
        minio = get_minio_client()
        if await minio.stat(bucket=state.bucket, key=state.object_key) is None:
            raise ResearchExportExpired(str(job_id))
        # Minted per request so a click-time status fetch holds a live link.
        url = await file_tickets.issue(
            bucket=state.bucket,
            key=state.object_key,
            content_type="application/zip",
            disposition=content_disposition("attachment", f"research-data-{state.created_at:%Y%m%d}.zip"),
        )
    return ResearchExportStatusOut(
        job_id=state.job_id,
        workspace_id=state.workspace_id,
        status=state.status,
        url=url,
        error=state.error,
    )


__all__ = ["export_router", "workspace_router"]
