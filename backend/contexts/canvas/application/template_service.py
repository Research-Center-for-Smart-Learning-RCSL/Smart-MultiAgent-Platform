"""Canvas template service -- orchestrates template CRUD and application."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.domain.models import (
    CanvasTemplate,
    CanvasTemplateScope,
)
from contexts.canvas.infrastructure.repositories import (
    CanvasRepository,
    CanvasTemplateRepository,
)
from shared_kernel import audit

_log = logging.getLogger(__name__)

_MAX_TEMPLATE_DATA_BYTES = 500 * 1024
_MAX_TEMPLATE_OBJECTS = 200


class TemplateDataTooLarge(Exception):
    pass


class TooManyTemplateObjects(Exception):
    pass


class CanvasNotEmpty(Exception):
    pass


class CanvasTemplateService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        room_channel_fn: Callable[[uuid.UUID], str] | None = None,
    ) -> None:
        self._db = db
        self._template_repo = CanvasTemplateRepository(db)
        self._canvas_repo = CanvasRepository(db)
        self._room_channel_fn = room_channel_fn or (lambda cid: f"ws:room:{cid}")

    async def list_templates(
        self,
        *,
        scope: CanvasTemplateScope | None = None,
        project_id: uuid.UUID | None = None,
    ) -> Sequence[CanvasTemplate]:
        return await self._template_repo.list_templates(scope=scope, project_id=project_id)

    async def get_template(self, template_id: uuid.UUID) -> CanvasTemplate | None:
        return await self._template_repo.get(template_id)

    def _validate_template_data(self, template_data: dict[str, Any]) -> None:
        raw = json.dumps(template_data, separators=(",", ":"))
        if len(raw.encode("utf-8")) > _MAX_TEMPLATE_DATA_BYTES:
            raise TemplateDataTooLarge(
                f"Template data exceeds {_MAX_TEMPLATE_DATA_BYTES // 1024} KB limit"
            )
        objects = template_data.get("objects", [])
        if not isinstance(objects, list):
            raise ValueError("template_data.objects must be a list")
        if len(objects) > _MAX_TEMPLATE_OBJECTS:
            raise TooManyTemplateObjects(
                f"Template has {len(objects)} objects (max {_MAX_TEMPLATE_OBJECTS})"
            )
        required_fields = {"kind", "position_x", "position_y", "width", "height"}
        for i, obj in enumerate(objects):
            if not isinstance(obj, dict):
                raise ValueError(f"template_data.objects[{i}] must be an object")
            missing = required_fields - obj.keys()
            if missing:
                raise ValueError(
                    f"template_data.objects[{i}] missing required fields: {', '.join(sorted(missing))}"
                )

    async def create_template(
        self,
        *,
        scope: CanvasTemplateScope,
        project_id: uuid.UUID | None,
        name: str,
        description: str | None,
        template_data: dict[str, Any],
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> CanvasTemplate:
        self._validate_template_data(template_data)
        values: dict[str, Any] = {
            "scope": scope.value,
            "project_id": project_id,
            "name": name,
            "description": description,
            "template_data": template_data,
            "created_by_user_id": actor_user_id,
        }
        template = await self._template_repo.create(values=values)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="canvas.template_created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="canvas_template",
                resource_id=template.id,
                metadata={"scope": scope.value, "name": name},
                request_id=request_id,
            ),
        )
        return template

    async def delete_template(
        self,
        template_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        deleted = await self._template_repo.soft_delete(template_id)
        if deleted:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="canvas.template_deleted",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="canvas_template",
                    resource_id=template_id,
                    metadata={},
                    request_id=request_id,
                ),
            )
        return deleted

    async def apply_template(
        self,
        *,
        canvas_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        template_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        actor_guest_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        template = await self._template_repo.get(template_id)
        if template is None:
            raise ValueError("Template not found")

        object_count = await self._canvas_repo.count_objects(canvas_id)
        if object_count > 0:
            raise CanvasNotEmpty("Canvas is not empty; clear it before applying a template")

        creates = []
        for obj in template.template_data.get("objects", []):
            creates.append({
                "kind": obj["kind"],
                "content": obj.get("content"),
                "position_x": obj.get("position_x", 0),
                "position_y": obj.get("position_y", 0),
                "width": obj.get("width", 100),
                "height": obj.get("height", 100),
                "z_index": obj.get("z_index", 0),
                "style": obj.get("style", {}),
            })

        from contexts.canvas.application.canvas_service import CanvasService

        svc = CanvasService(self._db, room_channel_fn=self._room_channel_fn)
        result = await svc.batch_operate(
            canvas_id=canvas_id,
            chatroom_id=chatroom_id,
            creates=creates,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            actor_guest_id=actor_guest_id,
            request_id=request_id,
        )

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="canvas.template_applied",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="canvas",
                resource_id=canvas_id,
                metadata={"template_id": str(template_id), "template_name": template.name},
                request_id=request_id,
            ),
        )
        return result

    async def create_from_canvas(
        self,
        *,
        canvas_id: uuid.UUID,
        project_id: uuid.UUID,
        name: str,
        description: str | None,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> CanvasTemplate:
        objects = await self._canvas_repo.list_objects(canvas_id)
        template_data: dict[str, Any] = {
            "objects": [
                {
                    "id": str(obj.id),
                    "kind": obj.kind.value,
                    "content": obj.content,
                    "position_x": obj.position_x,
                    "position_y": obj.position_y,
                    "width": obj.width,
                    "height": obj.height,
                    "z_index": obj.z_index,
                    "style": obj.style,
                }
                for obj in objects
            ]
        }
        return await self.create_template(
            scope=CanvasTemplateScope.PROJECT,
            project_id=project_id,
            name=name,
            description=description,
            template_data=template_data,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )


__all__ = [
    "CanvasNotEmpty",
    "CanvasTemplateService",
    "TemplateDataTooLarge",
    "TooManyTemplateObjects",
]
