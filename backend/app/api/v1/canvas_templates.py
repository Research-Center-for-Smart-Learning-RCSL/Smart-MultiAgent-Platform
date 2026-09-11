"""`/api/canvas-templates` -- Canvas template CRUD endpoints ([R13.59]-[R13.61])."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.application.template_service import (
    CanvasNotEmpty,
    TemplateDataTooLarge,
    TooManyTemplateObjects,
)
from contexts.canvas.domain.models import CanvasTemplateScope
from contexts.canvas.interfaces.facade import CanvasFacade
from contexts.conversation.application.access import (
    ensure_can_send,
    resolve_room_access,
)
from contexts.conversation.infrastructure.channels import room_channel
from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal, get_role_resolver
from shared_kernel.auth.permissions import Principal, Role, Scope
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/canvas-templates", tags=["canvas-templates"])
apply_router = APIRouter(prefix="/api/chatrooms/{chatroom_id}/canvas", tags=["canvas"])


# ---- Request/Response models -----------------------------------------------


class TemplateObjectIn(BaseModel):
    kind: str
    position_x: float
    position_y: float
    width: float
    height: float
    z_index: int = 0
    content: str | None = None
    style: dict[str, Any] | None = None

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        allowed = {"note", "text", "image", "shape", "drawing", "connector"}
        if v not in allowed:
            raise ValueError(f"kind must be one of {sorted(allowed)}")
        return v


class TemplateDataIn(BaseModel):
    objects: list[TemplateObjectIn] = Field(..., max_length=200)


class TemplateCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    project_id: uuid.UUID
    template_data: TemplateDataIn


class TemplateOut(BaseModel):
    id: uuid.UUID
    scope: str
    project_id: uuid.UUID | None
    name: str
    description: str | None
    created_by_user_id: uuid.UUID | None
    created_at: str

    @classmethod
    def from_domain(cls, t: Any) -> TemplateOut:
        return cls(
            id=t.id,
            scope=t.scope.value if hasattr(t.scope, "value") else t.scope,
            project_id=t.project_id,
            name=t.name,
            description=t.description,
            created_by_user_id=t.created_by_user_id,
            created_at=t.created_at.isoformat(),
        )


class TemplateDetailOut(TemplateOut):
    template_data: dict[str, Any]

    @classmethod
    def from_domain(cls, t: Any) -> TemplateDetailOut:
        return cls(
            id=t.id,
            scope=t.scope.value if hasattr(t.scope, "value") else t.scope,
            project_id=t.project_id,
            name=t.name,
            description=t.description,
            template_data=t.template_data,
            created_by_user_id=t.created_by_user_id,
            created_at=t.created_at.isoformat(),
        )


class ApplyTemplateIn(BaseModel):
    template_id: uuid.UUID


class SaveAsTemplateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)


# ---- Helpers ----------------------------------------------------------------


async def _ensure_project_moderator(
    principal: Principal,
    project_id: uuid.UUID,
    resolver: TenancyRoleResolver,
) -> None:
    if principal.is_admin:
        return
    roles = await resolver.roles_for(principal, Scope(project_id=project_id))
    if Role.PROJECT_OWNER not in roles and Role.ORG_OWNER not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Project admin required",
        )


# ---- Template CRUD ----------------------------------------------------------


@router.get("")
async def list_templates(
    scope: str | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[TemplateOut]:
    scope_enum: CanvasTemplateScope | None = None
    if scope is not None:
        try:
            scope_enum = CanvasTemplateScope(scope)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid scope: {scope}",
            ) from exc
    if project_id is not None and not principal.is_admin:
        resolver = TenancyRoleResolver(db)
        roles = await resolver.roles_for(principal, Scope(project_id=project_id))
        if not roles and not principal.is_guest:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not a project member",
            )

    facade = CanvasFacade(db)
    templates = await facade.list_templates(scope=scope_enum, project_id=project_id)
    return [TemplateOut.from_domain(t) for t in templates]


@router.get("/{template_id}")
async def get_template(
    template_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> TemplateDetailOut:
    facade = CanvasFacade(db)
    template = await facade.get_template(template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if template.scope == CanvasTemplateScope.PROJECT and template.project_id and not principal.is_admin:
        resolver = TenancyRoleResolver(db)
        roles = await resolver.roles_for(
            principal, Scope(project_id=template.project_id)
        )
        if not roles:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    return TemplateDetailOut.from_domain(template)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(
    body: TemplateCreateIn,
    principal: Principal = Depends(current_principal),
    resolver: TenancyRoleResolver = Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> TemplateOut:
    await _ensure_project_moderator(principal, body.project_id, resolver)
    facade = CanvasFacade(db)
    try:
        template = await facade.create_template(
            scope=CanvasTemplateScope.PROJECT,
            project_id=body.project_id,
            name=body.name,
            description=body.description,
            template_data=body.template_data.model_dump(),
            actor_user_id=principal.user_id if not principal.is_guest else None,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        )
    except (TemplateDataTooLarge, TooManyTemplateObjects) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    await db.commit()
    return TemplateOut.from_domain(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    resolver: TenancyRoleResolver = Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> None:
    facade = CanvasFacade(db)
    template = await facade.get_template(template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if template.scope == CanvasTemplateScope.PLATFORM:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform templates cannot be deleted",
        )

    if template.project_id:
        await _ensure_project_moderator(principal, template.project_id, resolver)

    deleted = await facade.delete_template(
        template_id,
        actor_user_id=principal.user_id if not principal.is_guest else None,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


# ---- Apply template (chatroom-scoped) --------------------------------------


@apply_router.post("/apply-template")
async def apply_template(
    chatroom_id: uuid.UUID,
    body: ApplyTemplateIn,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> dict[str, Any]:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_or_create(chatroom_id=chatroom_id)
    try:
        result = await facade.apply_template(
            canvas_id=canvas.id,
            chatroom_id=chatroom_id,
            template_id=body.template_id,
            actor_user_id=principal.user_id if not principal.is_guest else None,
            actor_ip=ctx.actor_ip,
            actor_guest_id=principal.user_id if principal.is_guest else None,
            request_id=ctx.request_id,
        )
    except CanvasNotEmpty as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Canvas is not empty",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await db.commit()
    created_out = []
    from app.api.v1.canvas import CanvasObjectOut

    for o in result.get("created", []):
        created_out.append(CanvasObjectOut.from_domain(o))
    return {"created": created_out, "updated": result["updated"], "deleted": result["deleted"]}


# ---- Save as template (chatroom-scoped) ------------------------------------


@apply_router.post("/save-as-template", status_code=status.HTTP_201_CREATED)
async def save_as_template(
    chatroom_id: uuid.UUID,
    body: SaveAsTemplateIn,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    ctx: RequestContext = Depends(current_context),
) -> TemplateOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)

    if not principal.is_admin and not access.is_moderator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Project admin required to save as template",
        )

    facade = CanvasFacade(db, room_channel_fn=room_channel)
    canvas = await facade.get_by_chatroom(chatroom_id)
    if canvas is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canvas not found")

    try:
        template = await facade.create_template_from_canvas(
            canvas_id=canvas.id,
            project_id=access.project_id,
            name=body.name,
            description=body.description,
            actor_user_id=principal.user_id if not principal.is_guest else None,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        )
    except (TemplateDataTooLarge, TooManyTemplateObjects) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    await db.commit()
    return TemplateOut.from_domain(template)
