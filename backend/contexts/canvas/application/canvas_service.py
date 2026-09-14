"""Canvas application service -- orchestrates domain logic for canvas CRUD."""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.domain.canvas_digest import build_canvas_digest
from contexts.canvas.domain.models import Canvas, CanvasObject, CanvasObjectKind, CanvasSnapshot
from contexts.canvas.infrastructure.channels import canvas_channel
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

    # ---- crdt state pass-throughs ------------------------------------------

    async def get_crdt_state(self, canvas_id: uuid.UUID) -> bytes | None:
        return await self._repo.get_crdt_state(canvas_id)

    async def update_crdt_state(self, canvas_id: uuid.UUID, state: bytes) -> None:
        await self._repo.update_crdt_state(canvas_id, state)

    async def sync_text_from_crdt(self, canvas_id: uuid.UUID) -> None:
        """Sync CRDT element text into canvas_objects for FTS search."""
        from contexts.canvas.application.crdt_relay import (
            _extract_elements_from_doc,
            get_crdt_relay,
        )

        relay = get_crdt_relay()
        elements = relay.extract_elements_for_digest(canvas_id)
        if elements is None:
            import pycrdt

            crdt_state = await self._repo.get_crdt_state(canvas_id)
            if not crdt_state:
                return
            doc: pycrdt.Doc = pycrdt.Doc()  # type: ignore[type-arg]
            doc.apply_update(crdt_state)
            elements = _extract_elements_from_doc(doc)
        await self._repo.sync_text_from_crdt(canvas_id, elements)

    def _enqueue_deferred_broadcast(
        self, canvas_id: uuid.UUID, delta_b64: str,
    ) -> None:
        """Schedule a CRDT broadcast to fire after the current transaction commits."""
        channel = canvas_channel(canvas_id)
        payload: dict[str, Any] = {"data": delta_b64}
        pending = self._db.info.setdefault("_pending_crdt_broadcasts", [])
        pending.append((channel, payload))
        if "_crdt_broadcast_hooked" not in self._db.info:
            self._db.info["_crdt_broadcast_hooked"] = True

            @event.listens_for(self._db.sync_session, "after_commit")
            def _drain(session: Any) -> None:
                broadcasts = list(session.info.pop("_pending_crdt_broadcasts", []))
                if not broadcasts:
                    return
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    return
                for ch, data in broadcasts:
                    loop.create_task(_best_effort_emit(ch, data))

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
        # Atomic upsert handles both the TOCTOU race (concurrent callers
        # that pass the SELECT above) and reactivation of a soft-deleted row.
        canvas = await self._repo.upsert_for_chatroom(chatroom_id)
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

    async def search_objects(
        self,
        canvas_id: uuid.UUID,
        query: str,
        *,
        limit: int = 50,
    ) -> Sequence[tuple[CanvasObject, float, str]]:
        return await self._repo.search(canvas_id, query, limit=limit)

    async def search_crdt_elements(
        self,
        canvas_id: uuid.UUID,
        query: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Substring search over Excalidraw text elements in the CRDT doc."""
        import pycrdt

        from contexts.canvas.application.crdt_relay import (
            _extract_elements_from_doc,
            get_crdt_relay,
        )

        relay = get_crdt_relay()
        if relay.has(canvas_id):
            elements = relay.extract_elements_for_digest(canvas_id) or []
        else:
            crdt_state = await self._repo.get_crdt_state(canvas_id)
            if not crdt_state:
                return []
            doc: pycrdt.Doc = pycrdt.Doc()  # type: ignore[type-arg]
            doc.apply_update(crdt_state)
            elements = _extract_elements_from_doc(doc)

        q_lower = query.lower()
        results: list[dict[str, Any]] = []
        for elem in elements:
            text = elem.get("text", "")
            if text and q_lower in text.lower():
                results.append(elem)
                if len(results) >= limit:
                    break
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

    async def get_snapshot(self, canvas_id: uuid.UUID, snapshot_id: uuid.UUID) -> CanvasSnapshot | None:
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
        from contexts.canvas.application.crdt_relay import get_crdt_relay
        from contexts.canvas.infrastructure.channels import canvas_channel

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

        snapshot_data = target.snapshot_data
        elements: list[dict[str, Any]] = []

        if "elements" in snapshot_data:
            elements = snapshot_data["elements"]
        elif "objects" in snapshot_data:
            from contexts.canvas.application.crdt_relay import build_element_dict

            for obj_data in snapshot_data["objects"]:
                kind = obj_data.get("kind", "shape")
                elem = build_element_dict(
                    kind,
                    elem_id=obj_data.get("id"),
                    x=obj_data.get("position_x", 0),
                    y=obj_data.get("position_y", 0),
                    width=obj_data.get("width", 100),
                    height=obj_data.get("height", 100),
                    content=obj_data.get("content"),
                    style=obj_data.get("style"),
                )
                elements.append(elem)

        relay = get_crdt_relay()
        crdt_state = await self._repo.get_crdt_state(canvas_id)
        full_state, delta = await relay.inject_elements(
            canvas_id,
            elements,
            crdt_state=crdt_state,
            replace=True,
        )
        await self._repo.update_crdt_state(canvas_id, full_state)
        await self._db.flush()

        delta_b64 = base64.b64encode(delta).decode("ascii")
        self._enqueue_deferred_broadcast(canvas_id, delta_b64)

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


async def _best_effort_emit(channel: str, data: dict[str, Any]) -> None:
    try:
        await Publisher(channel).emit("yjs-update", data)
    except Exception:
        _log.warning("deferred CRDT broadcast failed ch=%s", channel, exc_info=True)


__all__ = ["CanvasService"]
