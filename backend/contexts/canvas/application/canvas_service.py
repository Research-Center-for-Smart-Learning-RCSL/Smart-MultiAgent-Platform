"""Canvas application service -- orchestrates domain logic for canvas CRUD."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.domain.canvas_digest import build_canvas_digest
from contexts.canvas.domain.models import Canvas, CanvasObject, CanvasObjectKind, CanvasSnapshot
from contexts.canvas.infrastructure.repositories import CanvasRepository
from contexts.conversation.infrastructure.channels import room_channel
from shared_kernel import audit
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)

_MAX_IMAGES_PER_CANVAS = 50


class CanvasService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = CanvasRepository(db)

    # ---- canvas lifecycle --------------------------------------------------

    async def get_or_create(
        self,
        *,
        chatroom_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> Canvas:
        canvas = await self._repo.get_by_chatroom(chatroom_id)
        if canvas is not None:
            return canvas
        canvas = await self._repo.create(chatroom_id=chatroom_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="canvas.created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="canvas",
                resource_id=canvas.id,
                metadata={"chatroom_id": str(chatroom_id)},
                request_id=request_id,
            ),
        )
        return canvas

    async def update_settings(
        self,
        *,
        canvas_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        expose_to_agents: bool,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> Canvas | None:
        canvas = await self._repo.update_settings(canvas_id, expose_to_agents=expose_to_agents)
        if canvas is None:
            return None
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="canvas.settings_updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="canvas",
                resource_id=canvas_id,
                metadata={"expose_to_agents": expose_to_agents},
                request_id=request_id,
            ),
        )
        await Publisher(room_channel(chatroom_id)).emit(
            "canvas.settings_updated",
            {"canvas_id": str(canvas_id), "expose_to_agents": expose_to_agents},
        )
        return canvas

    async def delete(
        self,
        *,
        canvas_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._repo.soft_delete(canvas_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="canvas.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="canvas",
                resource_id=canvas_id,
                metadata={"chatroom_id": str(chatroom_id)},
                request_id=request_id,
            ),
        )

    # ---- objects -----------------------------------------------------------

    async def list_objects(
        self, canvas_id: uuid.UUID, *, limit: int = 500, offset: int = 0
    ) -> Sequence[CanvasObject]:
        return await self._repo.list_objects(canvas_id, limit=limit, offset=offset)

    async def create_object(
        self,
        *,
        canvas_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        kind: CanvasObjectKind,
        position_x: float,
        position_y: float,
        width: float,
        height: float,
        z_index: int = 0,
        content: str | None = None,
        minio_path: str | None = None,
        style: dict[str, Any] | None = None,
        created_by_user_id: uuid.UUID | None = None,
        created_by_guest_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> CanvasObject:
        obj = await self._repo.create_object(
            values={
                "canvas_id": canvas_id,
                "kind": kind.value,
                "position_x": position_x,
                "position_y": position_y,
                "width": width,
                "height": height,
                "z_index": z_index,
                "content": content,
                "minio_path": minio_path,
                "style": style or {},
                "created_by_user_id": created_by_user_id,
                "created_by_guest_id": created_by_guest_id,
            }
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="canvas.object_created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="canvas_object",
                resource_id=obj.id,
                metadata={"canvas_id": str(canvas_id), "kind": kind.value},
                request_id=request_id,
            ),
        )
        await Publisher(room_channel(chatroom_id)).emit(
            "canvas.object_created",
            {"canvas_id": str(canvas_id), "object_id": str(obj.id), "kind": kind.value},
        )
        return obj

    async def update_object(
        self,
        *,
        object_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        canvas_id: uuid.UUID,
        values: dict[str, Any],
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> CanvasObject | None:
        obj = await self._repo.update_object(object_id, values=values)
        if obj is None:
            return None
        await Publisher(room_channel(chatroom_id)).emit(
            "canvas.object_updated",
            {"canvas_id": str(canvas_id), "object_id": str(object_id)},
        )
        return obj

    async def delete_object(
        self,
        *,
        object_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        canvas_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        deleted = await self._repo.delete_object(object_id)
        if deleted:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="canvas.object_deleted",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="canvas_object",
                    resource_id=object_id,
                    metadata={"canvas_id": str(canvas_id)},
                    request_id=request_id,
                ),
            )
            await Publisher(room_channel(chatroom_id)).emit(
                "canvas.object_deleted",
                {"canvas_id": str(canvas_id), "object_id": str(object_id)},
            )
        return deleted

    async def batch_operate(
        self,
        *,
        canvas_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        creates: Sequence[dict[str, Any]] | None = None,
        updates: Sequence[dict[str, Any]] | None = None,
        deletes: Sequence[uuid.UUID] | None = None,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        actor_guest_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        results: dict[str, Any] = {"created": [], "updated": [], "deleted": 0}
        for item in creates or []:
            item["canvas_id"] = canvas_id
            if actor_user_id:
                item.setdefault("created_by_user_id", actor_user_id)
            if actor_guest_id:
                item.setdefault("created_by_guest_id", actor_guest_id)
            obj = await self._repo.create_object(values=item)
            results["created"].append(obj)
        for item in updates or []:
            oid = item.pop("id")
            await self._repo.update_object(oid, values=item)
            results["updated"].append(oid)
        if deletes:
            results["deleted"] = await self._repo.batch_delete_objects(deletes)
        await Publisher(room_channel(chatroom_id)).emit("canvas.batch_updated", {"canvas_id": str(canvas_id)})
        return results

    async def count_images(self, canvas_id: uuid.UUID) -> int:
        return await self._repo.count_images(canvas_id)

    @staticmethod
    def max_images_per_canvas() -> int:
        return _MAX_IMAGES_PER_CANVAS

    # ---- snapshots ---------------------------------------------------------

    async def list_snapshots(
        self, canvas_id: uuid.UUID, *, limit: int = 20, offset: int = 0
    ) -> Sequence[CanvasSnapshot]:
        return await self._repo.list_snapshots(canvas_id, limit=limit, offset=offset)

    async def create_snapshot(
        self,
        *,
        canvas_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> CanvasSnapshot:
        objects = await self._repo.list_objects(canvas_id)
        snapshot_data = {
            "objects": [
                {
                    "id": str(obj.id),
                    "kind": obj.kind.value,
                    "content": obj.content,
                    "minio_path": obj.minio_path,
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
        digest = build_canvas_digest(list(objects))
        snap = await self._repo.create_snapshot(
            values={
                "canvas_id": canvas_id,
                "snapshot_data": snapshot_data,
                "agent_digest": digest,
                "created_by_user_id": actor_user_id,
            }
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="canvas.snapshot_created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="canvas_snapshot",
                resource_id=snap.id,
                metadata={"canvas_id": str(canvas_id)},
                request_id=request_id,
            ),
        )
        await Publisher(room_channel(chatroom_id)).emit(
            "canvas.snapshot_created",
            {"canvas_id": str(canvas_id), "snapshot_id": str(snap.id)},
        )
        return snap

    async def latest_digest(self, canvas_id: uuid.UUID) -> str | None:
        snap = await self._repo.latest_snapshot(canvas_id)
        if snap and snap.agent_digest:
            return snap.agent_digest
        objects = await self._repo.list_objects(canvas_id)
        return build_canvas_digest(list(objects))


__all__ = ["CanvasService"]
