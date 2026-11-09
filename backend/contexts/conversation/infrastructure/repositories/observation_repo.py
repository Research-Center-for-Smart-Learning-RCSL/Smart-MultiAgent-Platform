"""Observation repository -- data access for observer-agent output (R28.03).

Observations live in their own table, never in ``messages``: every existing
message read path (REST list, WS fan-out, model history, search, compaction)
is structurally unable to see them.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.models import AgentObservation, AlertActionKind, AlertViewSurface
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
        is_alert=bool(row.is_alert),
        alert_reason=row.alert_reason,
        first_viewed_at=row.first_viewed_at,
        first_viewed_via=AlertViewSurface(row.first_viewed_via) if row.first_viewed_via else None,
        first_action_at=row.first_action_at,
        first_action_kind=AlertActionKind(row.first_action_kind) if row.first_action_kind else None,
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
        is_alert: bool = False,
        alert_reason: str | None = None,
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
                    is_alert=is_alert,
                    alert_reason=alert_reason if is_alert else None,
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

    async def list_for_rooms(
        self,
        *,
        chatroom_ids: Sequence[uuid.UUID],
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> Sequence[AgentObservation]:
        """All observations for a set of rooms ([R33.10])."""
        clauses: list[sa.ColumnElement[bool]] = [
            t.agent_observations.c.chatroom_id.in_(chatroom_ids),
            t.agent_observations.c.deleted_at.is_(None),
        ]
        if created_after:
            clauses.append(t.agent_observations.c.created_at >= created_after)
        if created_before:
            clauses.append(t.agent_observations.c.created_at < created_before)
        rows = (
            await self._db.execute(
                t.agent_observations.select()
                .where(sa.and_(*clauses))
                .order_by(t.agent_observations.c.created_at)
            )
        ).all()
        return [_row_to_observation(r) for r in rows]

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

    # ---- alert reaction times ([R34.02]) ------------------------------------
    #
    # Each "first" column is set by a conditional UPDATE that matches only while
    # it is still NULL, so concurrent reports leave exactly the earliest committed
    # one, and a later report is a no-op rather than an overwrite.

    async def mark_viewed(
        self,
        *,
        chatroom_id: uuid.UUID,
        observation_ids: Sequence[uuid.UUID],
        via: AlertViewSurface,
    ) -> int:
        if not observation_ids:
            return 0
        result = await self._db.execute(
            t.agent_observations.update()
            .where(
                t.agent_observations.c.chatroom_id == chatroom_id,
                t.agent_observations.c.id.in_(observation_ids),
                t.agent_observations.c.is_alert.is_(True),
                t.agent_observations.c.deleted_at.is_(None),
                t.agent_observations.c.first_viewed_at.is_(None),
            )
            .values(first_viewed_at=sa.func.now(), first_viewed_via=via.value)
        )
        return int(result.rowcount or 0)

    async def mark_first_action(
        self,
        *,
        chatroom_id: uuid.UUID,
        kind: AlertActionKind,
        observation_id: uuid.UUID | None = None,
    ) -> int:
        """Close the open alerts of a room raised up to now, or one alert.

        ``now()`` is the transaction's start time, so an alert recorded after the
        acting transaction began is not closed by it."""
        clauses: list[sa.ColumnElement[bool]] = [
            t.agent_observations.c.chatroom_id == chatroom_id,
            t.agent_observations.c.is_alert.is_(True),
            t.agent_observations.c.first_action_at.is_(None),
            t.agent_observations.c.created_at <= sa.func.now(),
        ]
        if observation_id is not None:
            clauses.append(t.agent_observations.c.id == observation_id)
        else:
            clauses.append(t.agent_observations.c.deleted_at.is_(None))
        result = await self._db.execute(
            t.agent_observations.update()
            .where(sa.and_(*clauses))
            .values(first_action_at=sa.func.now(), first_action_kind=kind.value)
        )
        return int(result.rowcount or 0)

    async def list_alerts(
        self,
        *,
        chatroom_ids: Sequence[uuid.UUID],
        include_deleted: bool,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[AgentObservation]:
        """Alerts of a set of rooms, newest first.

        The research export passes ``include_deleted``: deleting an alert is one of
        the reactions it measures, so a deleted alert is still a data point."""
        if not chatroom_ids:
            return []
        clauses: list[sa.ColumnElement[bool]] = [
            t.agent_observations.c.chatroom_id.in_(chatroom_ids),
            t.agent_observations.c.is_alert.is_(True),
        ]
        if not include_deleted:
            clauses.append(t.agent_observations.c.deleted_at.is_(None))
        stmt = (
            t.agent_observations.select()
            .where(sa.and_(*clauses))
            .order_by(t.agent_observations.c.created_at.desc(), t.agent_observations.c.id.desc())
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await self._db.execute(stmt)).all()
        return [_row_to_observation(r) for r in rows]


__all__ = ["ObservationRepository"]
