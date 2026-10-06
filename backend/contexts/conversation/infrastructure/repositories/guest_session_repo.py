"""Guest session repository -- data access for anonymous guest sessions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.models import GuestSession
from contexts.conversation.infrastructure import tables as t
from shared_kernel.auth.clients import now


def _row_to_guest_session(row: Any) -> GuestSession:
    return GuestSession(
        id=row.id,
        chatroom_id=row.chatroom_id,
        display_name=row.display_name,
        browser_id=row.browser_id,
        refresh_token_hash=row.refresh_token_hash,
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
    )


class GuestSessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        chatroom_id: uuid.UUID,
        display_name: str,
        browser_id: str | None,
        refresh_token_hash: str,
    ) -> GuestSession:
        row = (
            await self._db.execute(
                t.guest_sessions.insert()
                .values(
                    chatroom_id=chatroom_id,
                    display_name=display_name,
                    browser_id=browser_id,
                    refresh_token_hash=refresh_token_hash,
                )
                .returning(t.guest_sessions)
            )
        ).one()
        return _row_to_guest_session(row)

    async def list_labels(self, chatroom_id: uuid.UUID, *, limit: int = 1000) -> list[tuple[uuid.UUID, str]]:
        """``(id, display_name)`` of the room's guest sessions, most recent first.

        Projects the two columns only: the domain ``GuestSession`` carries the
        refresh-token hash and browser id, which a roster or a prompt label must
        never load. Served by ``ix_guest_sessions_chatroom_last_seen``.
        """
        rows = await self._db.execute(
            sa.select(t.guest_sessions.c.id, t.guest_sessions.c.display_name)
            .where(t.guest_sessions.c.chatroom_id == chatroom_id)
            .order_by(t.guest_sessions.c.last_seen_at.desc())
            .limit(limit)
        )
        return [(r.id, r.display_name) for r in rows]

    async def find_by_id(self, session_id: uuid.UUID) -> GuestSession | None:
        row = (
            await self._db.execute(t.guest_sessions.select().where(t.guest_sessions.c.id == session_id))
        ).one_or_none()
        return _row_to_guest_session(row) if row else None

    async def find_by_browser_id(self, *, chatroom_id: uuid.UUID, browser_id: str) -> GuestSession | None:
        """The browser's most recently seen session in the room.

        ``(chatroom_id, browser_id)`` is not unique, and rows duplicated by the
        join race fixed in 2026-10-05-guest-session-backend-hardening may exist,
        so this picks one rather than raising on every later resume.
        """
        row = (
            await self._db.execute(
                t.guest_sessions.select()
                .where(
                    sa.and_(
                        t.guest_sessions.c.chatroom_id == chatroom_id,
                        t.guest_sessions.c.browser_id == browser_id,
                    )
                )
                .order_by(t.guest_sessions.c.last_seen_at.desc())
                .limit(1)
            )
        ).first()
        return _row_to_guest_session(row) if row else None

    async def count_active(self, chatroom_id: uuid.UUID, *, since: datetime) -> int:
        result = await self._db.execute(
            sa.select(sa.func.count())
            .select_from(t.guest_sessions)
            .where(
                sa.and_(
                    t.guest_sessions.c.chatroom_id == chatroom_id,
                    t.guest_sessions.c.last_seen_at > since,
                )
            )
        )
        return result.scalar_one()

    async def rotate_refresh(
        self, *, old_hash: str, new_hash: str, chatroom_id: uuid.UUID
    ) -> GuestSession | None:
        """Swap the refresh hash in one statement; None when ``old_hash`` no longer
        names a session of this room ([R13.06b]).

        A compare-and-swap rather than read-then-write: under READ COMMITTED a
        concurrent rotation of the same cookie waits on the row lock, re-checks
        the predicate once the winner commits, and matches nothing.
        """
        row = (
            await self._db.execute(
                t.guest_sessions.update()
                .where(
                    sa.and_(
                        t.guest_sessions.c.refresh_token_hash == old_hash,
                        t.guest_sessions.c.chatroom_id == chatroom_id,
                    )
                )
                .values(refresh_token_hash=new_hash, last_seen_at=now())
                .returning(t.guest_sessions)
            )
        ).first()
        return _row_to_guest_session(row) if row else None

    async def update_last_seen(self, session_id: uuid.UUID) -> None:
        await self._db.execute(
            t.guest_sessions.update().where(t.guest_sessions.c.id == session_id).values(last_seen_at=now())
        )

    async def update_display_name(self, session_id: uuid.UUID, display_name: str) -> None:
        await self._db.execute(
            t.guest_sessions.update()
            .where(t.guest_sessions.c.id == session_id)
            .values(display_name=display_name)
        )

    async def update_refresh_hash(self, session_id: uuid.UUID, refresh_token_hash: str) -> None:
        await self._db.execute(
            t.guest_sessions.update()
            .where(t.guest_sessions.c.id == session_id)
            .values(refresh_token_hash=refresh_token_hash, last_seen_at=now())
        )

    async def delete_older_than(self, cutoff: datetime) -> int:
        result = await self._db.execute(
            t.guest_sessions.delete().where(t.guest_sessions.c.last_seen_at < cutoff)
        )
        return result.rowcount or 0
