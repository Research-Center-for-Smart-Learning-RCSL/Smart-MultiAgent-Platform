"""Per-room activity time limit overrides ([R30.46])."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.infrastructure import tables as t

_T = t.chatroom_activity_time_limits


class RoomTimeLimitRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def for_room(self, chatroom_id: uuid.UUID) -> dict[uuid.UUID, int | None]:
        """Overrides keyed by type id; a ``None`` value is an explicit "no limit"."""
        rows = (
            await self._db.execute(
                sa.select(_T.c.activity_type_id, _T.c.time_limit_minutes).where(
                    _T.c.chatroom_id == chatroom_id
                )
            )
        ).all()
        return {r.activity_type_id: r.time_limit_minutes for r in rows}

    async def replace(self, chatroom_id: uuid.UUID, limits: Mapping[uuid.UUID, int | None]) -> None:
        """Make the room's overrides exactly ``limits`` (caller owns commit)."""
        await self._db.execute(_T.delete().where(_T.c.chatroom_id == chatroom_id))
        if not limits:
            return
        await self._db.execute(
            pg_insert(_T).values(
                [
                    {"chatroom_id": chatroom_id, "activity_type_id": type_id, "time_limit_minutes": minutes}
                    for type_id, minutes in limits.items()
                ]
            )
        )


__all__ = ["RoomTimeLimitRepository"]
