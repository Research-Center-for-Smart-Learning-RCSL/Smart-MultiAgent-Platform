"""Comment application service -- orchestrates CRUD for canvas object comments."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.domain.models import CanvasComment
from contexts.canvas.infrastructure.repositories import CanvasCommentRepository
from shared_kernel import audit
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)

_MAX_COMMENT_LENGTH = 2000


def _sanitize_content(text: str) -> str:
    cleaned = "".join(ch for ch in text if ch in {"\n", "\t"} or not ch.isascii() or (" " <= ch < "\x7f"))
    return cleaned[:_MAX_COMMENT_LENGTH]


class CommentService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        room_channel_fn: Callable[[uuid.UUID], str] | None = None,
    ) -> None:
        self._db = db
        self._repo = CanvasCommentRepository(db)
        self._room_channel_fn = room_channel_fn or (lambda cid: f"ws:room:{cid}")

    async def list_comments(
        self,
        object_id: uuid.UUID,
        *,
        canvas_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[CanvasComment]:
        return await self._repo.list_comments(object_id, canvas_id=canvas_id, limit=limit, offset=offset)

    async def get_comment(self, comment_id: uuid.UUID, *, canvas_id: uuid.UUID) -> CanvasComment | None:
        return await self._repo.get(comment_id, canvas_id=canvas_id)

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
        sanitized = _sanitize_content(content)
        comment = await self._repo.create_comment(
            values={
                "canvas_id": canvas_id,
                "object_id": object_id,
                "content": sanitized,
                "created_by_user_id": created_by_user_id,
                "created_by_guest_id": created_by_guest_id,
            }
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="canvas.comment_created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="canvas_comment",
                resource_id=comment.id,
                metadata={
                    "canvas_id": str(canvas_id),
                    "object_id": str(object_id),
                },
                request_id=request_id,
            ),
        )
        await Publisher(self._room_channel_fn(chatroom_id)).emit(
            "canvas.comment_created",
            {
                "canvas_id": str(canvas_id),
                "object_id": str(object_id),
                "comment_id": str(comment.id),
            },
        )
        return comment

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
        sanitized = _sanitize_content(content)
        comment = await self._repo.update_comment(comment_id, canvas_id=canvas_id, content=sanitized)
        if comment is None:
            return None
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="canvas.comment_updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="canvas_comment",
                resource_id=comment_id,
                metadata={"canvas_id": str(canvas_id)},
                request_id=request_id,
            ),
        )
        await Publisher(self._room_channel_fn(chatroom_id)).emit(
            "canvas.comment_updated",
            {
                "canvas_id": str(canvas_id),
                "object_id": str(comment.object_id),
                "comment_id": str(comment_id),
            },
        )
        return comment

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
        deleted = await self._repo.delete_comment(comment_id, canvas_id=canvas_id)
        if deleted:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="canvas.comment_deleted",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="canvas_comment",
                    resource_id=comment_id,
                    metadata={"canvas_id": str(canvas_id)},
                    request_id=request_id,
                ),
            )
            await Publisher(self._room_channel_fn(chatroom_id)).emit(
                "canvas.comment_deleted",
                {
                    "canvas_id": str(canvas_id),
                    "object_id": str(object_id),
                    "comment_id": str(comment_id),
                },
            )
        return deleted

    async def count_comments_by_object(
        self,
        object_ids: Sequence[uuid.UUID],
        *,
        canvas_id: uuid.UUID,
    ) -> dict[uuid.UUID, int]:
        return await self._repo.count_comments_by_object(object_ids, canvas_id=canvas_id)


__all__ = ["CommentService"]
