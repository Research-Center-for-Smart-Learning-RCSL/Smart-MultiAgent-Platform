"""Canvas application service -- orchestrates domain logic for canvas CRUD."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.domain.canvas_digest import build_canvas_digest
from contexts.canvas.domain.models import Canvas, CanvasObject, CanvasObjectKind, CanvasSnapshot
from contexts.canvas.infrastructure.repositories import CanvasRepository
from shared_kernel import audit
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)

_MAX_IMAGES_PER_CANVAS = 50
_MAX_SNAPSHOTS_PER_CANVAS = 50

_ALLOWED_UPDATE_FIELDS = frozenset(
    {
        "position_x",
        "position_y",
        "width",
        "height",
        "z_index",
        "content",
        "style",
    }
)


class CanvasService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        room_channel_fn: Callable[[uuid.UUID], str] | None = None,
    ) -> None:
        self._db = db
        self._repo = CanvasRepository(db)
        self._room_channel_fn = room_channel_fn or (lambda cid: f"ws:room:{cid}")

    # ---- canvas lifecycle --------------------------------------------------

    async def get_by_chatroom(self, chatroom_id: uuid.UUID) -> Canvas | None:
        return await self._repo.get_by_chatroom(chatroom_id)

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
        await Publisher(self._room_channel_fn(chatroom_id)).emit(
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
        created_by_agent_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> CanvasObject:
        vals: dict[str, Any] = {
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
        if created_by_agent_id is not None:
            vals["created_by_agent_id"] = created_by_agent_id
        obj = await self._repo.create_object(values=vals)
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
        event_data: dict[str, Any] = {
            "canvas_id": str(canvas_id),
            "object_id": str(obj.id),
            "kind": kind.value,
        }
        if created_by_agent_id is not None:
            event_data["agent_id"] = str(created_by_agent_id)
        await Publisher(self._room_channel_fn(chatroom_id)).emit(
            "canvas.object_created",
            event_data,
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
        obj = await self._repo.update_object(object_id, canvas_id=canvas_id, values=values)
        if obj is None:
            return None
        await Publisher(self._room_channel_fn(chatroom_id)).emit(
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
        deleted = await self._repo.delete_object(object_id, canvas_id=canvas_id)
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
            await Publisher(self._room_channel_fn(chatroom_id)).emit(
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
            create_vals = {**item, "canvas_id": canvas_id}
            if actor_user_id:
                create_vals.setdefault("created_by_user_id", actor_user_id)
            if actor_guest_id:
                create_vals.setdefault("created_by_guest_id", actor_guest_id)
            obj = await self._repo.create_object(values=create_vals)
            results["created"].append(obj)
        for item in updates or []:
            oid = item.get("id")
            if oid is None:
                continue
            filtered = {k: v for k, v in item.items() if k in _ALLOWED_UPDATE_FIELDS}
            if filtered:
                updated = await self._repo.update_object(oid, canvas_id=canvas_id, values=filtered)
                if updated is not None:
                    results["updated"].append(oid)
        if deletes:
            results["deleted"] = await self._repo.batch_delete_objects(deletes, canvas_id=canvas_id)
        channel = self._room_channel_fn(chatroom_id)
        await Publisher(channel).emit("canvas.batch_updated", {"canvas_id": str(canvas_id)})
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

    async def get_snapshot(
        self, canvas_id: uuid.UUID, snapshot_id: uuid.UUID
    ) -> CanvasSnapshot | None:
        return await self._repo.get_snapshot(snapshot_id, canvas_id=canvas_id)

    async def create_snapshot(
        self,
        *,
        canvas_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        label: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> CanvasSnapshot:
        from contexts.canvas.application.canvas_context_provider import (
            elements_to_pseudo_objects,
        )
        from contexts.canvas.application.crdt_relay import get_crdt_relay

        count = await self._repo.count_snapshots(canvas_id)
        if count >= _MAX_SNAPSHOTS_PER_CANVAS:
            await self._repo.delete_oldest_snapshot(canvas_id)

        relay = get_crdt_relay()
        crdt_elements = relay.extract_elements_for_digest(canvas_id) if relay.has(canvas_id) else None

        if crdt_elements is not None:
            snapshot_data = {"elements": crdt_elements}
            pseudo_objects = elements_to_pseudo_objects(crdt_elements)
            digest = build_canvas_digest(pseudo_objects)
        else:
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

        values: dict[str, Any] = {
            "canvas_id": canvas_id,
            "snapshot_data": snapshot_data,
            "agent_digest": digest,
            "created_by_user_id": actor_user_id,
        }
        if label is not None:
            values["label"] = label

        snap = await self._repo.create_snapshot(values=values)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="canvas.snapshot_created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="canvas_snapshot",
                resource_id=snap.id,
                metadata={"canvas_id": str(canvas_id), "label": label},
                request_id=request_id,
            ),
        )
        await Publisher(self._room_channel_fn(chatroom_id)).emit(
            "canvas.snapshot_created",
            {"canvas_id": str(canvas_id), "snapshot_id": str(snap.id)},
        )
        return snap

    async def restore_snapshot(
        self,
        *,
        canvas_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> CanvasSnapshot | None:
        target = await self._repo.get_snapshot(snapshot_id, canvas_id=canvas_id)
        if target is None:
            return None

        auto_save = await self.create_snapshot(
            canvas_id=canvas_id,
            chatroom_id=chatroom_id,
            label="Auto-save before restore",
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

        all_objects = await self._repo.list_objects(canvas_id)
        if all_objects:
            await self._repo.batch_delete_objects(
                [obj.id for obj in all_objects], canvas_id=canvas_id
            )

        snapshot_data = target.snapshot_data
        if "elements" in snapshot_data:
            for elem in snapshot_data["elements"]:
                kind_str = elem.get("type", "shape")
                kind_map = {
                    "rectangle": "shape",
                    "ellipse": "shape",
                    "diamond": "shape",
                    "line": "connector",
                    "arrow": "connector",
                    "freedraw": "drawing",
                    "text": "text",
                    "image": "image",
                }
                mapped_kind = kind_map.get(kind_str, "shape")
                await self._repo.create_object(
                    values={
                        "canvas_id": canvas_id,
                        "kind": mapped_kind,
                        "content": elem.get("text"),
                        "position_x": elem.get("x", 0),
                        "position_y": elem.get("y", 0),
                        "width": elem.get("width", 100),
                        "height": elem.get("height", 100),
                        "z_index": 0,
                        "style": {},
                        "created_by_user_id": actor_user_id,
                    }
                )
        elif "objects" in snapshot_data:
            for obj_data in snapshot_data["objects"]:
                await self._repo.create_object(
                    values={
                        "canvas_id": canvas_id,
                        "kind": obj_data.get("kind", "shape"),
                        "content": obj_data.get("content"),
                        "minio_path": obj_data.get("minio_path"),
                        "position_x": obj_data.get("position_x", 0),
                        "position_y": obj_data.get("position_y", 0),
                        "width": obj_data.get("width", 100),
                        "height": obj_data.get("height", 100),
                        "z_index": obj_data.get("z_index", 0),
                        "style": obj_data.get("style", {}),
                        "created_by_user_id": actor_user_id,
                    }
                )

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="canvas.snapshot_restored",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="canvas_snapshot",
                resource_id=snapshot_id,
                metadata={
                    "canvas_id": str(canvas_id),
                    "auto_save_id": str(auto_save.id),
                },
                request_id=request_id,
            ),
        )
        await Publisher(self._room_channel_fn(chatroom_id)).emit(
            "canvas.snapshot_restored",
            {
                "canvas_id": str(canvas_id),
                "restored_snapshot_id": str(snapshot_id),
                "auto_save_snapshot_id": str(auto_save.id),
            },
        )
        return auto_save

    async def latest_digest(self, canvas_id: uuid.UUID) -> str | None:
        from contexts.canvas.application.canvas_context_provider import (
            elements_to_pseudo_objects,
        )
        from contexts.canvas.application.crdt_relay import get_crdt_relay

        # Try CRDT first
        relay = get_crdt_relay()
        if relay.has(canvas_id):
            elements = relay.extract_elements_for_digest(canvas_id)
            if elements:
                pseudo_objects = elements_to_pseudo_objects(elements)
                return build_canvas_digest(pseudo_objects)

        # Try persisted crdt_state
        crdt_state = await self._repo.get_crdt_state(canvas_id)
        if crdt_state:
            from contexts.canvas.application.canvas_context_provider import (
                _digest_from_crdt_state,
            )

            digest = _digest_from_crdt_state(crdt_state)
            if digest:
                return digest

        # Fall back to snapshot then objects
        snap = await self._repo.latest_snapshot(canvas_id)
        if snap and snap.agent_digest:
            return snap.agent_digest
        objects = await self._repo.list_objects(canvas_id)
        return build_canvas_digest(list(objects))


__all__ = ["CanvasService"]
