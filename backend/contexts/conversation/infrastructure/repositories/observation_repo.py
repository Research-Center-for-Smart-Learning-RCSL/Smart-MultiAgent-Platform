"""Observation repository -- data access for observer-agent output (R28.03).

Observations live in their own table, never in ``messages``: every existing
message read path (REST list, WS fan-out, model history, search, compaction)
is structurally unable to see them.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.models import AgentObservation
from contexts.conversation.infrastructure import tables as t
from shared_kernel.auth.clients import now


def _row_to_observation(row: Any) -> AgentObservation:
    return AgentObservation(
        id=row.id,
        chatroom_id=row.chatroom_id,
        agent_id=row.agent_id,
        content_md=row.content_md,
        metadata=dict(row.metadata or {}),
        blocks=list(row.blocks or []),
        trigger=row.trigger,
        trigger_message_id=row.trigger_message_id,
        released_at=row.released_at,
        release_target=dict(row.release_target) if row.release_target else None,
        released_by_user_id=row.released_by_user_id,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


class ObservationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        content_md: str,
        trigger: str,
        trigger_message_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> AgentObservation:
        row = (
            await self._db.execute(
                t.agent_observations.insert()
                .values(
                    chatroom_id=chatroom_id,
                    agent_id=agent_id,
                    content_md=content_md,
                    trigger=trigger,
                    trigger_message_id=trigger_message_id,
                    metadata=metadata or {},
                    blocks=blocks or [],
                )
                .returning(t.agent_observations)
            )
        ).one()
        return _row_to_observation(row)

    async def get(
        self,
        *,
        chatroom_id: uuid.UUID,
        observation_id: uuid.UUID,
    ) -> AgentObservation | None:
        # Room-scoped on purpose: the chatroom id is part of the AuthZ
        # boundary, so an id from another room is indistinguishable from a
        # missing one.
        row = (
            await self._db.execute(
                t.agent_observations.select().where(
                    sa.and_(
                        t.agent_observations.c.id == observation_id,
                        t.agent_observations.c.chatroom_id == chatroom_id,
                        t.agent_observations.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_observation(row) if row else None

    async def list(
        self,
        *,
        chatroom_id: uuid.UUID,
        before: uuid.UUID | None = None,
        limit: int = 50,
    ) -> Sequence[AgentObservation]:
        predicate: sa.ColumnElement[bool] = sa.and_(
            t.agent_observations.c.chatroom_id == chatroom_id,
            t.agent_observations.c.deleted_at.is_(None),
        )
        if before is not None:
            anchor = (
                await self._db.execute(
                    sa.select(t.agent_observations.c.created_at, t.agent_observations.c.id).where(
                        sa.and_(
                            t.agent_observations.c.id == before,
                            # Scoped by room like the page query below — an id
                            # from a different room the caller isn't the
                            # creator of must 404 like any other missing
                            # cursor, not silently page this room using it.
                            t.agent_observations.c.chatroom_id == chatroom_id,
                        )
                    )
                )
            ).first()
            if anchor is None:
                raise ValueError(f"cursor 'before' observation {before} not found")
            predicate = sa.and_(
                predicate,
                sa.or_(
                    t.agent_observations.c.created_at < anchor.created_at,
                    sa.and_(
                        t.agent_observations.c.created_at == anchor.created_at,
                        t.agent_observations.c.id < anchor.id,
                    ),
                ),
            )
        rows = (
            await self._db.execute(
                t.agent_observations.select()
                .where(predicate)
                .order_by(t.agent_observations.c.created_at.desc(), t.agent_observations.c.id.desc())
                .limit(limit)
            )
        ).all()
        return [_row_to_observation(r) for r in rows]

    async def list_recent_for_agent(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        limit: int = 10,
    ) -> Sequence[AgentObservation]:
        """Newest *limit* observations by one agent, returned oldest-first.

        Feeds the observer's self-memory block (R28.05), which reads
        chronologically.
        """
        rows = (
            await self._db.execute(
                t.agent_observations.select()
                .where(
                    sa.and_(
                        t.agent_observations.c.chatroom_id == chatroom_id,
                        t.agent_observations.c.agent_id == agent_id,
                        t.agent_observations.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.agent_observations.c.created_at.desc(), t.agent_observations.c.id.desc())
                .limit(limit)
            )
        ).all()
        return [_row_to_observation(r) for r in reversed(rows)]

    async def mark_released(
        self,
        *,
        chatroom_id: uuid.UUID,
        observation_id: uuid.UUID,
        released_by_user_id: uuid.UUID,
        release_target: dict[str, Any],
    ) -> bool:
        """CAS: exactly one concurrent release wins (R28.08)."""
        result = await self._db.execute(
            t.agent_observations.update()
            .where(
                sa.and_(
                    t.agent_observations.c.id == observation_id,
                    t.agent_observations.c.chatroom_id == chatroom_id,
                    t.agent_observations.c.deleted_at.is_(None),
                    t.agent_observations.c.released_at.is_(None),
                )
            )
            .values(
                released_at=now(),
                released_by_user_id=released_by_user_id,
                release_target=release_target,
            )
        )
        return bool(result.rowcount)

    async def mark_release_message(
        self,
        *,
        chatroom_id: uuid.UUID,
        observation_id: uuid.UUID,
        release_target: dict[str, Any],
    ) -> None:
        """Backfill the room-message id into ``release_target``.

        The release CAS runs *before* the system message is inserted (so a
        losing racer never inserts an orphan message); the winner stamps the
        message id here in the same transaction.
        """
        await self._db.execute(
            t.agent_observations.update()
            .where(
                sa.and_(
                    t.agent_observations.c.id == observation_id,
                    t.agent_observations.c.chatroom_id == chatroom_id,
                    t.agent_observations.c.released_at.is_not(None),
                )
            )
            .values(release_target=release_target)
        )

    async def soft_delete(
        self,
        *,
        chatroom_id: uuid.UUID,
        observation_id: uuid.UUID,
    ) -> bool:
        result = await self._db.execute(
            t.agent_observations.update()
            .where(
                sa.and_(
                    t.agent_observations.c.id == observation_id,
                    t.agent_observations.c.chatroom_id == chatroom_id,
                    t.agent_observations.c.deleted_at.is_(None),
                )
            )
            .values(deleted_at=now())
        )
        return bool(result.rowcount)


__all__ = ["ObservationRepository"]
