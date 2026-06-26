"""Chatroom repositories -- data access for chatroom, agent, and guest entities."""

from __future__ import annotations

import base64
import secrets
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import VersionMismatch
from contexts.conversation.domain.models import (
    Chatroom,
    ChatroomAgent,
    ChatroomGuest,
)
from contexts.conversation.infrastructure import tables as t
from shared_kernel.auth.clients import now


def _row_to_chatroom(row: Any) -> Chatroom:
    return Chatroom(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        allow_org_members=row.allow_org_members,
        allow_project_members=row.allow_project_members,
        allow_project_owners_only=row.allow_project_owners_only,
        allow_guest_links=row.allow_guest_links,
        guest_token=row.guest_token,
        version=row.version,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


def _new_guest_token() -> str:
    # R13.05 -- 32 CSPRNG bytes = 256 bits of entropy. base64url encodes to 43
    # chars after stripping the single trailing '=' padding character. The '='
    # carries no entropy and is omitted so the token is URL-safe without escaping.
    return (
        base64.urlsafe_b64encode(secrets.token_bytes(32))
        .rstrip(b"=")
        .decode(
            "ascii",
        )
    )


class ChatroomRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        workspace_id: uuid.UUID,
        name: str,
        allow_org_members: bool = False,
        allow_project_members: bool = True,
        allow_project_owners_only: bool = False,
        allow_guest_links: bool = False,
    ) -> Chatroom:
        token = _new_guest_token()
        row = (
            await self._db.execute(
                t.chatrooms.insert()
                .values(
                    workspace_id=workspace_id,
                    name=name,
                    allow_org_members=allow_org_members,
                    allow_project_members=allow_project_members,
                    allow_project_owners_only=allow_project_owners_only,
                    allow_guest_links=allow_guest_links,
                    guest_token=token,
                )
                .returning(t.chatrooms)
            )
        ).one()
        return _row_to_chatroom(row)

    async def get(
        self,
        chatroom_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> Chatroom | None:
        predicate = t.chatrooms.c.id == chatroom_id
        if not include_deleted:
            predicate = sa.and_(predicate, t.chatrooms.c.deleted_at.is_(None))
        row = (await self._db.execute(t.chatrooms.select().where(predicate))).first()
        return _row_to_chatroom(row) if row else None

    async def get_by_guest_token(self, token: str) -> Chatroom | None:
        row = (
            await self._db.execute(
                t.chatrooms.select().where(
                    sa.and_(
                        t.chatrooms.c.guest_token == token,
                        t.chatrooms.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_chatroom(row) if row else None

    async def list_ids_for_project(self, project_id: uuid.UUID) -> list[uuid.UUID]:
        """All live chatroom ids whose workspace belongs to *project_id*.

        Scopes chatroom references for the workflow linter (rule 8): a workflow
        may reference any chatroom in its workspace's project, across that
        project's workspaces.
        """
        rows = (
            await self._db.execute(
                sa.select(t.chatrooms.c.id)
                .join(t.workspaces, t.chatrooms.c.workspace_id == t.workspaces.c.id)
                .where(
                    sa.and_(
                        t.workspaces.c.project_id == project_id,
                        t.chatrooms.c.deleted_at.is_(None),
                        t.workspaces.c.deleted_at.is_(None),
                    )
                )
            )
        ).all()
        return [r.id for r in rows]

    async def list_for_workspace(
        self,
        workspace_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Chatroom]:
        rows = (
            await self._db.execute(
                t.chatrooms.select()
                .where(
                    sa.and_(
                        t.chatrooms.c.workspace_id == workspace_id,
                        t.chatrooms.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.chatrooms.c.created_at)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [_row_to_chatroom(r) for r in rows]

    async def count_active_in_workspace(self, workspace_id: uuid.UUID) -> int:
        row = (
            await self._db.execute(
                sa.select(sa.func.count())
                .select_from(t.chatrooms)
                .where(
                    sa.and_(
                        t.chatrooms.c.workspace_id == workspace_id,
                        t.chatrooms.c.deleted_at.is_(None),
                    )
                )
            )
        ).one()
        return int(row[0])

    async def update(
        self,
        *,
        chatroom_id: uuid.UUID,
        expected_version: int,
        values: dict[str, Any],
    ) -> Chatroom:
        stmt = (
            t.chatrooms.update()
            .where(
                sa.and_(
                    t.chatrooms.c.id == chatroom_id,
                    t.chatrooms.c.version == expected_version,
                    t.chatrooms.c.deleted_at.is_(None),
                )
            )
            .values(**values)  # version bumped by smap_bump_version trigger
            .returning(t.chatrooms)
        )
        row = (await self._db.execute(stmt)).first()
        if row is None:
            raise VersionMismatch(f"chatroom {chatroom_id} version mismatch or missing")
        return _row_to_chatroom(row)

    async def soft_delete(self, chatroom_id: uuid.UUID) -> None:
        await self._db.execute(
            t.chatrooms.update().where(t.chatrooms.c.id == chatroom_id).values(deleted_at=now())
        )


class ChatroomAgentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(self, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> None:
        stmt = (
            pg.insert(t.chatroom_agents)
            .values(
                chatroom_id=chatroom_id,
                agent_id=agent_id,
            )
            .on_conflict_do_nothing()
        )
        await self._db.execute(stmt)

    async def remove(self, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> None:
        await self._db.execute(
            t.chatroom_agents.delete().where(
                sa.and_(
                    t.chatroom_agents.c.chatroom_id == chatroom_id,
                    t.chatroom_agents.c.agent_id == agent_id,
                )
            )
        )

    async def list(self, chatroom_id: uuid.UUID) -> Sequence[ChatroomAgent]:
        rows = (
            await self._db.execute(
                t.chatroom_agents.select().where(
                    t.chatroom_agents.c.chatroom_id == chatroom_id,
                )
            )
        ).all()
        return [ChatroomAgent(chatroom_id=r.chatroom_id, agent_id=r.agent_id) for r in rows]

    async def is_registered(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> bool:
        row = (
            await self._db.execute(
                sa.select(sa.literal(1))
                .select_from(t.chatroom_agents)
                .where(
                    sa.and_(
                        t.chatroom_agents.c.chatroom_id == chatroom_id,
                        t.chatroom_agents.c.agent_id == agent_id,
                    )
                )
            )
        ).first()
        return row is not None

    async def list_live_bindings(
        self,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> list[tuple[uuid.UUID, uuid.UUID]]:  # type: ignore[valid-type]
        """Return (agent_id, chatroom_id) pairs for non-deleted chatrooms.

        Used by the silence-trigger sweep (M19) so the SQL join stays in the
        repository instead of the worker task.
        """
        rows = (
            await self._db.execute(
                sa.select(
                    t.chatroom_agents.c.agent_id,
                    t.chatroom_agents.c.chatroom_id,
                )
                .select_from(
                    t.chatroom_agents.join(
                        t.chatrooms,
                        t.chatrooms.c.id == t.chatroom_agents.c.chatroom_id,
                    )
                )
                .where(t.chatrooms.c.deleted_at.is_(None))
                .order_by(t.chatroom_agents.c.chatroom_id, t.chatroom_agents.c.agent_id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [(r.agent_id, r.chatroom_id) for r in rows]


class ChatroomGuestRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(
        self,
        *,
        chatroom_id: uuid.UUID,
        user_id: uuid.UUID,
        joined_via_token: str,
        display_name: str | None = None,
    ) -> None:
        stmt = (
            pg.insert(t.chatroom_guests)
            .values(
                chatroom_id=chatroom_id,
                user_id=user_id,
                joined_via_token=joined_via_token,
                display_name=display_name,
            )
            .on_conflict_do_nothing()
        )
        await self._db.execute(stmt)

    async def is_guest(
        self,
        *,
        chatroom_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        row = (
            await self._db.execute(
                sa.select(sa.literal(1))
                .select_from(t.chatroom_guests)
                .where(
                    sa.and_(
                        t.chatroom_guests.c.chatroom_id == chatroom_id,
                        t.chatroom_guests.c.user_id == user_id,
                    )
                )
            )
        ).first()
        return row is not None

    async def list(self, chatroom_id: uuid.UUID) -> Sequence[ChatroomGuest]:
        rows = (
            await self._db.execute(
                t.chatroom_guests.select().where(
                    t.chatroom_guests.c.chatroom_id == chatroom_id,
                )
            )
        ).all()
        return [
            ChatroomGuest(
                chatroom_id=r.chatroom_id,
                user_id=r.user_id,
                joined_via_token=r.joined_via_token,
                display_name=r.display_name,
                joined_at=r.joined_at,
            )
            for r in rows
        ]


__all__ = [
    "ChatroomAgentRepository",
    "ChatroomGuestRepository",
    "ChatroomRepository",
    "_new_guest_token",
]
