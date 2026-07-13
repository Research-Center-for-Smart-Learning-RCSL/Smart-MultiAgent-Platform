"""`/api/admin/projects` + `/api/admin/restore` — project listing & restore."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.identity.interfaces.facade import IdentityFacade
from contexts.tenancy.domain.models import Project
from contexts.tenancy.interfaces.facade import TenancyFacade
from contexts.workflow.interfaces.facade import WorkflowFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.restore import RestoreConflict
from shared_kernel.db.session import db_session

RestoreType = Literal["user", "org", "project", "agent", "workflow", "chatroom"]

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ProjectSummaryOut(BaseModel):
    id: uuid.UUID
    name: str
    owner_user_id: uuid.UUID | None
    owner_org_id: uuid.UUID | None
    deleted_at: str | None
    created_at: str


class RestoreOut(BaseModel):
    restored: bool


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@router.get("/projects")
async def list_projects(
    cursor: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> list[ProjectSummaryOut]:
    import sqlalchemy as sa

    if cursor is not None:
        q = sa.text(
            "SELECT id, name, owner_user_id, owner_org_id, deleted_at, created_at "
            "FROM projects "
            "WHERE created_at < (SELECT created_at FROM projects WHERE id = :cursor) "
            "OR (created_at = (SELECT created_at FROM projects WHERE id = :cursor) AND id < :cursor) "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        ).bindparams(limit=limit, cursor=cursor)
    else:
        q = sa.text(
            "SELECT id, name, owner_user_id, owner_org_id, deleted_at, created_at "
            "FROM projects ORDER BY created_at DESC, id DESC LIMIT :limit"
        ).bindparams(limit=limit)
    rows = (await db.execute(q)).all()
    return [
        ProjectSummaryOut(
            id=r[0],
            name=r[1],
            owner_user_id=r[2],
            owner_org_id=r[3],
            deleted_at=r[4].isoformat() if r[4] else None,
            created_at=r[5].isoformat(),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Restore (R8.13)
# ---------------------------------------------------------------------------


def _is_live(row: object | None) -> bool:
    """A row is live iff it exists and is not soft-deleted.

    Most `get_*` readers already filter soft-deleted rows (so `None` == gone),
    but the identity user reader does not — hence the explicit `deleted_at`
    check, which also covers the project -> owning-user hop."""
    return row is not None and getattr(row, "deleted_at", None) is None


def _parent_deleted(resource_type: str, parent_type: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=f"Cannot restore this {resource_type}: its {parent_type} is deleted. "
        f"Restore the {parent_type} first.",
    )


async def _assert_project_owner_live(db: AsyncSession, resource_type: str, project: Project) -> None:
    """A project is owned by an org XOR a user (DB CHECK); its live owner is the
    top of the chain."""
    if project.owner_org_id is not None:
        org = await TenancyFacade(db).get_org(project.owner_org_id)
        if not _is_live(org):
            raise _parent_deleted(resource_type, "org")
    elif project.owner_user_id is not None:
        user = await IdentityFacade(db).get_user(project.owner_user_id)
        if not _is_live(user):
            raise _parent_deleted(resource_type, "user")


async def _assert_project_ancestor_live(db: AsyncSession, resource_type: str, project_id: uuid.UUID) -> None:
    """Verify an ancestor project (soft-deleted project reads as `None`) and its
    owner are live."""
    project = await TenancyFacade(db).get_project(project_id)
    if project is None:
        raise _parent_deleted(resource_type, "project")
    await _assert_project_owner_live(db, resource_type, project)


async def _assert_workspace_ancestor_live(
    db: AsyncSession, resource_type: str, workspace_id: uuid.UUID
) -> None:
    workspace = await ConversationFacade(db).get_workspace(workspace_id)
    if workspace is None:
        raise _parent_deleted(resource_type, "workspace")
    await _assert_project_ancestor_live(db, resource_type, workspace.project_id)


async def _assert_ancestors_live(
    db: AsyncSession, resource_type: RestoreType, resource_id: uuid.UUID
) -> None:
    """Reject restoring a soft-deleted child whose ancestor chain is not fully
    live (R8.13 parent-liveness guard).

    Route-level orchestration: every hop is resolved through the owning
    context's facade, so no context reads another's tables. A missing or
    already-live child falls through to the normal restore (which yields 404 /
    a no-op), leaving the existing not-found and already-live semantics intact.
    `user`/`org` have no ancestor and are not handled here."""
    if resource_type == "project":
        project = await TenancyFacade(db).get_project(resource_id, include_deleted=True)
        if project is None or project.deleted_at is None:
            return
        await _assert_project_owner_live(db, resource_type, project)
    elif resource_type == "agent":
        agent = await AgentsFacade(db).get_agent(resource_id, include_deleted=True)
        if agent is None or agent.deleted_at is None:
            return
        await _assert_project_ancestor_live(db, resource_type, agent.project_id)
    elif resource_type == "workflow":
        workflow = await WorkflowFacade(db).get_workflow(resource_id, include_deleted=True)
        if workflow is None or workflow.deleted_at is None:
            return
        await _assert_workspace_ancestor_live(db, resource_type, workflow.workspace_id)
    elif resource_type == "chatroom":
        chatroom = await ConversationFacade(db).get_chatroom(resource_id, include_deleted=True)
        if chatroom is None or chatroom.deleted_at is None:
            return
        await _assert_workspace_ancestor_live(db, resource_type, chatroom.workspace_id)


@router.post("/restore/{resource_type}/{resource_id}")
async def restore_resource(
    resource_type: RestoreType = Path(...),
    resource_id: uuid.UUID = Path(...),
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> RestoreOut:
    # Each type is restored by the facade of the context that owns its data
    # (R8.13). The lambda defers construction so only the selected facade is built.
    restorers: dict[RestoreType, Callable[[], Awaitable[bool]]] = {
        "user": lambda: IdentityFacade(db).restore_user(
            resource_id=resource_id,
            admin_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        ),
        "org": lambda: TenancyFacade(db).restore_org(
            resource_id=resource_id,
            admin_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        ),
        "project": lambda: TenancyFacade(db).restore_project(
            resource_id=resource_id,
            admin_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        ),
        "agent": lambda: AgentsFacade(db).restore_agent(
            resource_id=resource_id,
            admin_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        ),
        "workflow": lambda: WorkflowFacade(db).restore_workflow(
            resource_id=resource_id,
            admin_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        ),
        "chatroom": lambda: ConversationFacade(db).restore_chatroom(
            resource_id=resource_id,
            admin_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        ),
    }
    try:
        await _assert_ancestors_live(db, resource_type, resource_id)
        restored = await restorers[resource_type]()
    except RestoreConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot restore this {exc.resource_type}: another active "
            f"{exc.resource_type} already uses its unique name or email.",
        ) from exc
    if not restored:
        raise HTTPException(status_code=404, detail="Resource not found or not soft-deleted")
    return RestoreOut(restored=True)
