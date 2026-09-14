"""Canvas write tools -- create, update, delete canvas objects for a granted agent ([R13.57]).

Separate from ``builtin_tools`` for the same reasons ``activity_tools`` and
``draft_tools`` are: the dependency on the canvas context stays in one file,
and this is not a tool any ``agent_tools`` row can produce.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.runtime.tool_registry import Tool, ToolResult, clip_tool_output
from contexts.agents.domain.models import Agent

logger = logging.getLogger(__name__)

TOOL_CANVAS_CREATE = "canvas_create_object"
TOOL_CANVAS_UPDATE = "canvas_update_object"
TOOL_CANVAS_DELETE = "canvas_delete_object"

_CANVAS_OBJECT_KINDS = ["note", "text", "shape", "drawing", "connector"]


@dataclass(frozen=True, slots=True)
class CanvasWriteContext:
    chatroom_id: uuid.UUID
    canvas_id: uuid.UUID
    agent_id: uuid.UUID
    actor_user_id: uuid.UUID
    actor_ip: str | None
    request_id: uuid.UUID | None


async def resolve_canvas_write(
    db: AsyncSession, *, chatroom_id: uuid.UUID | None, agent_id: uuid.UUID
) -> CanvasWriteContext | None:
    """This turn's canvas-writing authority, or ``None``.

    Fails closed on everything: no room, no grant, no canvas, or any exception
    yields ``None`` and therefore no tools.
    """
    if chatroom_id is None:
        return None
    try:
        from contexts.conversation.infrastructure.repositories import ChatroomAgentRepository

        grant = await ChatroomAgentRepository(db).canvas_write_grant(
            chatroom_id=chatroom_id,
            agent_id=agent_id,
        )
        if grant is None:
            return None

        from contexts.canvas.interfaces.facade import CanvasFacade

        facade = CanvasFacade(db)
        canvas = await facade.get_by_chatroom(chatroom_id)
        if canvas is None:
            return None

        return CanvasWriteContext(
            chatroom_id=chatroom_id,
            canvas_id=canvas.id,
            agent_id=agent_id,
            actor_user_id=grant.granted_by_user_id,
            actor_ip=None,
            request_id=None,
        )
    except Exception:
        logger.warning(
            "canvas write resolution failed for agent %s in room %s; offering no tools",
            agent_id,
            chatroom_id,
            exc_info=True,
        )
        return None


def build_canvas_write_tools(
    db: AsyncSession,
    *,
    agent: Agent,
    context: CanvasWriteContext,
) -> list[Tool]:
    """``canvas_create_object``, ``canvas_update_object``, ``canvas_delete_object``."""
    return [
        _build_create_tool(db, agent=agent, ctx=context),
        _build_update_tool(db, agent=agent, ctx=context),
        _build_delete_tool(db, agent=agent, ctx=context),
    ]


def _build_create_tool(db: AsyncSession, *, agent: Agent, ctx: CanvasWriteContext) -> Tool:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": _CANVAS_OBJECT_KINDS,
                "description": "Object type to create.",
            },
            "position_x": {"type": "number", "description": "X position on the canvas."},
            "position_y": {"type": "number", "description": "Y position on the canvas."},
            "width": {"type": "number", "description": "Width of the object."},
            "height": {"type": "number", "description": "Height of the object."},
            "content": {"type": "string", "description": "Text content of the object."},
            "style": {
                "type": "object",
                "description": "Visual style overrides (JSON object).",
            },
        },
        "required": ["kind", "position_x", "position_y", "width", "height"],
        "additionalProperties": False,
    }

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        from contexts.canvas.interfaces.facade import CanvasFacade, build_element_dict
        from shared_kernel import audit

        kind_str = str(args.get("kind", ""))
        if kind_str not in _CANVAS_OBJECT_KINDS:
            return ToolResult(content=f"Invalid kind: {kind_str}", is_error=True)

        elem_id = str(uuid.uuid4())
        element = build_element_dict(
            kind_str,
            elem_id=elem_id,
            x=float(args.get("position_x", 0)),
            y=float(args.get("position_y", 0)),
            width=float(args.get("width", 100)),
            height=float(args.get("height", 100)),
            content=args.get("content"),
            style=args.get("style"),
            custom_data={"createdByAgentId": str(ctx.agent_id)},
        )

        facade = CanvasFacade(db)
        full_state, delta = await facade.crdt_inject_elements(
            ctx.canvas_id, [element],
        )
        await facade.persist_and_broadcast_crdt(
            ctx.canvas_id,
            full_state=full_state,
            delta=delta,
            deferred=True,
        )

        await audit.emit(
            db,
            audit.AuditEvent(
                action="canvas.agent_object_created",
                actor_user_id=ctx.actor_user_id,
                actor_ip=ctx.actor_ip,
                resource_type="canvas_object",
                resource_id=uuid.UUID(elem_id),
                metadata={
                    "agent_id": str(ctx.agent_id),
                    "canvas_id": str(ctx.canvas_id),
                    "kind": kind_str,
                },
                request_id=ctx.request_id,
            ),
            isolated=True,
        )
        return ToolResult(
            content=clip_tool_output(f"Created canvas object {elem_id} (kind={kind_str})."),
        )

    return Tool(
        name=TOOL_CANVAS_CREATE,
        description="Create a new object on the room's canvas.",
        input_schema=schema,
        invoke=_invoke,
    )


def _build_update_tool(db: AsyncSession, *, agent: Agent, ctx: CanvasWriteContext) -> Tool:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "object_id": {"type": "string", "description": "UUID of the object to update."},
            "position_x": {"type": "number", "description": "New X position."},
            "position_y": {"type": "number", "description": "New Y position."},
            "width": {"type": "number", "description": "New width."},
            "height": {"type": "number", "description": "New height."},
            "content": {"type": "string", "description": "New text content."},
            "style": {"type": "object", "description": "New style overrides."},
        },
        "required": ["object_id"],
        "additionalProperties": False,
    }

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        from contexts.canvas.interfaces.facade import CanvasFacade, CrdtUpdateError
        from shared_kernel import audit

        raw_id = str(args.get("object_id", ""))
        try:
            uuid.UUID(raw_id)
        except ValueError:
            return ToolResult(content=f"Invalid object_id: {raw_id}", is_error=True)

        field_map: dict[str, str] = {
            "position_x": "x",
            "position_y": "y",
            "width": "width",
            "height": "height",
            "content": "text",
        }
        crdt_fields: dict[str, Any] = {}
        original_keys: list[str] = []
        for key in ("position_x", "position_y", "width", "height", "content"):
            if key in args:
                crdt_fields[field_map[key]] = args[key]
                original_keys.append(key)
        style = args.get("style")
        if style:
            original_keys.append("style")
            if "backgroundColor" in style:
                crdt_fields["backgroundColor"] = style["backgroundColor"]
            if "strokeColor" in style:
                crdt_fields["strokeColor"] = style["strokeColor"]
        if not crdt_fields:
            return ToolResult(content="No fields to update.", is_error=True)

        facade = CanvasFacade(db)
        try:
            full_state, delta = await facade.crdt_update_element(
                ctx.canvas_id, raw_id, crdt_fields,
            )
        except CrdtUpdateError:
            return ToolResult(content="Object not found.", is_error=True)
        await facade.persist_and_broadcast_crdt(
            ctx.canvas_id,
            full_state=full_state,
            delta=delta,
            deferred=True,
        )

        await audit.emit(
            db,
            audit.AuditEvent(
                action="canvas.agent_object_updated",
                actor_user_id=ctx.actor_user_id,
                actor_ip=ctx.actor_ip,
                resource_type="canvas_object",
                resource_id=uuid.UUID(raw_id),
                metadata={
                    "agent_id": str(ctx.agent_id),
                    "canvas_id": str(ctx.canvas_id),
                    "fields": original_keys,
                },
                request_id=ctx.request_id,
            ),
            isolated=True,
        )
        return ToolResult(content=clip_tool_output(f"Updated canvas object {raw_id}."))

    return Tool(
        name=TOOL_CANVAS_UPDATE,
        description="Update an existing object on the room's canvas.",
        input_schema=schema,
        invoke=_invoke,
    )


def _build_delete_tool(db: AsyncSession, *, agent: Agent, ctx: CanvasWriteContext) -> Tool:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "object_id": {"type": "string", "description": "UUID of the object to delete."},
        },
        "required": ["object_id"],
        "additionalProperties": False,
    }

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        from contexts.canvas.interfaces.facade import CanvasFacade, CrdtUpdateError
        from shared_kernel import audit

        raw_id = str(args.get("object_id", ""))
        try:
            uuid.UUID(raw_id)
        except ValueError:
            return ToolResult(content=f"Invalid object_id: {raw_id}", is_error=True)

        facade = CanvasFacade(db)
        try:
            full_state, delta = await facade.crdt_delete_element(
                ctx.canvas_id, raw_id,
            )
        except CrdtUpdateError:
            return ToolResult(content="Object not found.", is_error=True)
        await facade.persist_and_broadcast_crdt(
            ctx.canvas_id,
            full_state=full_state,
            delta=delta,
            deferred=True,
        )

        await audit.emit(
            db,
            audit.AuditEvent(
                action="canvas.agent_object_deleted",
                actor_user_id=ctx.actor_user_id,
                actor_ip=ctx.actor_ip,
                resource_type="canvas_object",
                resource_id=uuid.UUID(raw_id),
                metadata={
                    "agent_id": str(ctx.agent_id),
                    "canvas_id": str(ctx.canvas_id),
                },
                request_id=ctx.request_id,
            ),
            isolated=True,
        )
        return ToolResult(content=clip_tool_output(f"Deleted canvas object {raw_id}."))

    return Tool(
        name=TOOL_CANVAS_DELETE,
        description="Delete an object from the room's canvas.",
        input_schema=schema,
        invoke=_invoke,
    )


__all__ = [
    "CanvasWriteContext",
    "build_canvas_write_tools",
    "resolve_canvas_write",
]
