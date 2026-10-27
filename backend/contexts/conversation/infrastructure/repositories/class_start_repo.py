"""A room's autonomous class start ([R15.27]): its setting and its once-per-class claim."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.models import ClassStartConfig, ClassStartState
from contexts.conversation.infrastructure import tables as t

_R = t.chatrooms
_COLUMNS = (_R.c.id, _R.c.class_start_config, _R.c.class_first_arrival_at, _R.c.class_started_at)


def _state(row: Any) -> ClassStartState:
    return ClassStartState(
        chatroom_id=row.id,
        config=ClassStartConfig.from_stored(row.class_start_config),
        first_arrival_at=row.class_first_arrival_at,
        started_at=row.class_started_at,
    )


def _live(chatroom_id: uuid.UUID) -> sa.ColumnElement[bool]:
    return sa.and_(_R.c.id == chatroom_id, _R.c.deleted_at.is_(None))


def _armed() -> sa.ColumnElement[bool]:
    # Matches ix_chatrooms_pending_class_start (0106), so the sweep reads only the index.
    return sa.and_(_R.c.class_start_config.isnot(None), _R.c.class_started_at.is_(None))


class ClassStartRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, chatroom_id: uuid.UUID) -> ClassStartState | None:
        row = (await self._db.execute(sa.select(*_COLUMNS).where(_live(chatroom_id)))).first()
        return _state(row) if row is not None else None

    async def awaiting_start(self, *, limit: int) -> list[ClassStartState]:
        """Armed rooms someone has already arrived in: the ones the max wait can open."""
        rows = (
            await self._db.execute(
                sa.select(*_COLUMNS)
                .where(
                    sa.and_(
                        _armed(),
                        _R.c.class_first_arrival_at.isnot(None),
                        _R.c.deleted_at.is_(None),
                    )
                )
                .order_by(_R.c.class_first_arrival_at)
                .limit(limit)
            )
        ).all()
        return [_state(r) for r in rows]

    async def record_first_arrival(self, chatroom_id: uuid.UUID) -> None:
        """Start the max-wait clock once per class; a later arrival never moves it."""
        await self._db.execute(
            _R.update()
            .where(sa.and_(_live(chatroom_id), _armed(), _R.c.class_first_arrival_at.is_(None)))
            .values(class_first_arrival_at=sa.func.now())
        )

    async def claim_start(self, chatroom_id: uuid.UUID) -> bool:
        """Mark the class started; ``True`` for exactly one caller per class."""
        result = await self._db.execute(
            _R.update()
            .where(sa.and_(_live(chatroom_id), _armed()))
            .values(class_started_at=sa.func.now())
            .returning(_R.c.id)
        )
        return result.first() is not None

    async def write(
        self, chatroom_id: uuid.UUID, *, config: ClassStartConfig | None, reset: bool
    ) -> ClassStartState | None:
        """Replace the setting; ``reset`` re-arms the room for another class."""
        # sa.null(), not None: a JSONB column stores None as the JSON literal null,
        # which IS NOT NULL and would leave the room in the sweep's index forever.
        values: dict[str, Any] = {"class_start_config": config.to_stored() if config else sa.null()}
        if reset:
            values["class_started_at"] = None
            values["class_first_arrival_at"] = None
        row = (
            await self._db.execute(
                _R.update().where(_live(chatroom_id)).values(**values).returning(*_COLUMNS)
            )
        ).first()
        return _state(row) if row is not None else None


__all__ = ["ClassStartRepository"]
