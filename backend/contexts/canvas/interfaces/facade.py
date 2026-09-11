"""Canvas facade -- the public surface for routes and other contexts.

Thin pass-throughs to the application service (caller owns commit).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.application.canvas_service import CanvasService
from contexts.canvas.application.comment_service import CommentService
from contexts.canvas.domain.models import Canvas, CanvasComment, CanvasObject, CanvasObjectKind, CanvasSnapshot


class CanvasFacade:
    def __init__(
        self,
        db: AsyncSession,
        *,
        room_channel_fn: Callable[[uuid.UUID], str] | None = None,
    ) -> None:
        self._db = db
        self._service = CanvasService(db, room_channel_fn=room_channel_fn)
        self._comments = CommentService(db, room_channel_fn=room_channel_fn)

    async def get_by_chatroom(self, chatroom_id: uuid.UUID) -> Canvas | None:
        return await self._service.get_by_chatroom(chatroom_id)

    async def get_or_create(
        self,
        *,
        chatroom_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> Canvas:
        return await self._service.get_or_create(
            chatroom_id=chatroom_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

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
        return await self._service.update_settings(
            canvas_id=canvas_id,
            chatroom_id=chatroom_id,
            expose_to_agents=expose_to_agents,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def delete(
        self,
        *,
        canvas_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        return await self._service.delete(
            canvas_id=canvas_id,
            chatroom_id=chatroom_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def list_objects(
        self, canvas_id: uuid.UUID, *, limit: int = 500, offset: int = 0
    ) -> Sequence[CanvasObject]:
        return await self._service.list_objects(canvas_id, limit=limit, offset=offset)

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
        return await self._service.create_object(
            canvas_id=canvas_id,
            chatroom_id=chatroom_id,
            kind=kind,
            position_x=position_x,
            position_y=position_y,
            width=width,
            height=height,
            z_index=z_index,
            content=content,
            minio_path=minio_path,
            style=style,
            created_by_user_id=created_by_user_id,
            created_by_guest_id=created_by_guest_id,
            created_by_agent_id=created_by_agent_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

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
        return await self._service.update_object(
            object_id=object_id,
            chatroom_id=chatroom_id,
            canvas_id=canvas_id,
            values=values,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

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
        return await self._service.delete_object(
            object_id=object_id,
            chatroom_id=chatroom_id,
            canvas_id=canvas_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

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
        return await self._service.batch_operate(
            canvas_id=canvas_id,
            chatroom_id=chatroom_id,
            creates=creates,
            updates=updates,
            deletes=deletes,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            actor_guest_id=actor_guest_id,
            request_id=request_id,
        )

    async def count_images(self, canvas_id: uuid.UUID) -> int:
        return await self._service.count_images(canvas_id)

    def max_images_per_canvas(self) -> int:
        return self._service.max_images_per_canvas()

    async def list_snapshots(
        self, canvas_id: uuid.UUID, *, limit: int = 20, offset: int = 0
    ) -> Sequence[CanvasSnapshot]:
        return await self._service.list_snapshots(canvas_id, limit=limit, offset=offset)

    async def create_snapshot(
        self,
        *,
        canvas_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> CanvasSnapshot:
        return await self._service.create_snapshot(
            canvas_id=canvas_id,
            chatroom_id=chatroom_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )


    # ---- comments ------------------------------------------------------------

    async def list_comments(
        self,
        object_id: uuid.UUID,
        *,
        canvas_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[CanvasComment]:
        return await self._comments.list_comments(
            object_id, canvas_id=canvas_id, limit=limit, offset=offset
        )

    async def get_comment(
        self, comment_id: uuid.UUID, *, canvas_id: uuid.UUID
    ) -> CanvasComment | None:
        return await self._comments.get_comment(comment_id, canvas_id=canvas_id)

    async def create_comment(
        self,
        *,
        canvas_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        object_id: uuid.UUID,
        content: str,
        created_by_user_id: uuid.UUID | None = None,
        created_by_guest_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> CanvasComment:
        return await self._comments.create_comment(
            canvas_id=canvas_id,
            chatroom_id=chatroom_id,
            object_id=object_id,
            content=content,
            created_by_user_id=created_by_user_id,
            created_by_guest_id=created_by_guest_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def update_comment(
        self,
        *,
        comment_id: uuid.UUID,
        canvas_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        content: str,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> CanvasComment | None:
        return await self._comments.update_comment(
            comment_id=comment_id,
            canvas_id=canvas_id,
            chatroom_id=chatroom_id,
            content=content,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def delete_comment(
        self,
        *,
        comment_id: uuid.UUID,
        canvas_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        object_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        return await self._comments.delete_comment(
            comment_id=comment_id,
            canvas_id=canvas_id,
            chatroom_id=chatroom_id,
            object_id=object_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def count_comments_by_object(
        self,
        object_ids: Sequence[uuid.UUID],
        *,
        canvas_id: uuid.UUID,
    ) -> dict[uuid.UUID, int]:
        return await self._comments.count_comments_by_object(
            object_ids, canvas_id=canvas_id
        )


__all__ = ["CanvasFacade"]
