"""Persistence for the room-level activity activation invariant."""

from __future__ import annotations

import uuid
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy import CursorResult
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import ActivationStatus, ActivityActivation
from contexts.activities.infrastructure import tables as t
from shared_kernel.auth.clients import now

_ACTIVATION_COLS = (
    t.activity_activations.c.id,
    t.activity_activations.c.chatroom_id,
    t.activity_activations.c.activity_type_id,
    t.activity_activations.c.started_by_user_id,
    t.activity_activations.c.status,
    t.activity_activations.c.created_at,
    t.activity_activations.c.ended_at,
)


def _row_to_activation(row: object) -> ActivityActivation:
    return ActivityActivation(
        id=row.id,  # type: ignore[attr-defined]
        chatroom_id=row.chatroom_id,  # type: ignore[attr-defined]
        activity_type_id=row.activity_type_id,  # type: ignore[attr-defined]
        started_by_user_id=row.started_by_user_id,  # type: ignore[attr-defined]
        status=ActivationStatus(row.status),  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
        ended_at=row.ended_at,  # type: ignore[attr-defined]
    )


class ActivationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, activation_id: uuid.UUID) -> ActivityActivation | None:
        row = (
            await self._db.execute(
                sa.select(*_ACTIVATION_COLS).where(t.activity_activations.c.id == activation_id)
            )
        ).first()
        return _row_to_activation(row) if row is not None else None

    async def get_active(self, chatroom_id: uuid.UUID) -> ActivityActivation | None:
        return await self._get_active(chatroom_id)

    async def get_active_for_update(self, chatroom_id: uuid.UUID) -> ActivityActivation | None:
        """Lock the active row until the caller commits, serializing end with submit."""
        return await self._get_active(chatroom_id, for_update=True)

    async def _get_active(
        self, chatroom_id: uuid.UUID, *, for_update: bool = False
    ) -> ActivityActivation | None:
        statement = sa.select(*_ACTIVATION_COLS).where(
            sa.and_(
                t.activity_activations.c.chatroom_id == chatroom_id,
                t.activity_activations.c.status == ActivationStatus.ACTIVE.value,
            )
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._db.execute(statement)).first()
        return _row_to_activation(row) if row is not None else None

    async def create_active(
        self, *, chatroom_id: uuid.UUID, activity_type_id: uuid.UUID, started_by_user_id: uuid.UUID
    ) -> uuid.UUID | None:
        result = await self._db.execute(
            pg_insert(t.activity_activations)
            .values(
                chatroom_id=chatroom_id,
                activity_type_id=activity_type_id,
                started_by_user_id=started_by_user_id,
                status=ActivationStatus.ACTIVE.value,
            )
            .on_conflict_do_nothing()
            .returning(t.activity_activations.c.id)
        )
        row = result.first()
        return row.id if row is not None else None

    async def end(self, activation_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            t.activity_activations.update()
            .where(
                sa.and_(
                    t.activity_activations.c.id == activation_id,
                    t.activity_activations.c.status == ActivationStatus.ACTIVE.value,
                )
            )
            .values(status=ActivationStatus.ENDED.value, ended_at=now())
        )
        return bool(cast("CursorResult[Any]", result).rowcount)


__all__ = ["ActivationRepository"]
