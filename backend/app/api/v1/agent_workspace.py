"""`/api/agents/{id}/workspace-files` — designer file uploads for Code Interpreter.

AuthZ: ``RESOURCE_CREATE_EDIT`` at the agent's project scope (the designer).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.agent_service import AgentService
from contexts.agents.application.workspace_service import WorkspaceFileService
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    current_context,
    current_principal,
)
from shared_kernel.auth.permissions import Capability, Principal
from shared_kernel.db.session import db_session
from shared_kernel.storage import get_minio_client

router = APIRouter(prefix="/api/agents/{agent_id}/workspace-files", tags=["agent-workspace"])


class WorkspaceFileOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    path: str
    size_bytes: int
    mime: str
    created_at: str


def _to_out(f) -> WorkspaceFileOut:
    return WorkspaceFileOut(
        id=f.id,
        agent_id=f.agent_id,
        path=f.path,
        size_bytes=f.size_bytes,
        mime=f.mime,
        created_at=f.created_at.isoformat(),
    )


async def _require_edit(
    agent_id: uuid.UUID,
    principal: Principal,
    db: AsyncSession,
) -> None:
    service = AgentService(db)
    agent = await service.get(agent_id)

    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=agent.project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_workspace_file(
    agent_id: uuid.UUID = Path(...),
    file: UploadFile = File(...),
    path: str | None = Form(default=None),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> WorkspaceFileOut:
    await _require_edit(agent_id, principal, db)

    max_upload = 32 * 1024 * 1024
    if file.size is not None and file.size > max_upload:
        raise HTTPException(status_code=413, detail="file exceeds 32 MB limit")
    data = await file.read()
    if len(data) > max_upload:
        raise HTTPException(status_code=413, detail="file exceeds 32 MB limit")
    mime = file.content_type or "application/octet-stream"
    filename = file.filename or "file"

    service = WorkspaceFileService(db, get_minio_client())
    try:
        wf = await service.upload(
            agent_id=agent_id,
            filename=filename,
            data=data,
            mime=mime,
            path=path,
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_out(wf)


@router.get("")
async def list_workspace_files(
    agent_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[WorkspaceFileOut]:
    service = AgentService(db)
    agent = await service.get(agent_id)

    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope

    if not principal.is_admin:
        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(principal, Scope(project_id=agent.project_id))
        if not roles:
            _raise_forbidden("caller is not a member of the agent's project")

    ws_service = WorkspaceFileService(db, get_minio_client())
    files = await ws_service.list_files(agent_id)
    return [_to_out(f) for f in files]


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_workspace_file(
    agent_id: uuid.UUID = Path(...),
    file_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _require_edit(agent_id, principal, db)

    service = WorkspaceFileService(db, get_minio_client())
    await service.delete(
        agent_id=agent_id,
        file_id=file_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


__all__ = ["router"]
