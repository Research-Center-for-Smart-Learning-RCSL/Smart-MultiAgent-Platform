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
    ActivityControlGrant,
    Chatroom,
    ChatroomAgent,
    ChatroomAgentRole,
    ChatroomGuest,
)
from contexts.conversation.infrastructure import tables as t
from shared_kernel.auth.clients import now


def _allowlist_from_json(value: Any) -> tuple[uuid.UUID, ...]:
    """Parse the stored ``activity_type_allowlist`` into type ids.

    Total, and lossy on purpose. The column is `jsonb` with no per-element schema,
    so an entry that is not a UUID string is dropped rather than raised on: the
    write route validates every id before it lands, and if something ever bypasses
    that, "this type is not offered" is the safe reading — the failure mode of
    raising here would be an agent's whole turn, or a room's whole settings page,
    lost to one bad row.
    """
    if not isinstance(value, list):
        return ()
    out: list[uuid.UUID] = []
    for item in value:
        try:
            out.append(uuid.UUID(str(item)))
        except (AttributeError, TypeError, ValueError):
            continue
    return tuple(out)


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
        created_by_user_id=row.created_by_user_id,
        disclose_observers=row.disclose_observers,
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
        created_by_user_id: uuid.UUID | None = None,
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
                    created_by_user_id=created_by_user_id,
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

    async def get_many(self, chatroom_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Chatroom]:
        """Batch-resolve live rooms by id, keyed by id, for N+1-free name lookups.

        Mirrors ``TenancyFacade.get_projects``. Exists so a cross-context caller can
        name rooms without joining this context's tables.
        """
        ids = list(chatroom_ids)
        if not ids:
            return {}
        rows = (
            await self._db.execute(
                t.chatrooms.select().where(
                    sa.and_(t.chatrooms.c.id.in_(ids), t.chatrooms.c.deleted_at.is_(None))
                )
            )
        ).all()
        rooms = [_row_to_chatroom(r) for r in rows]
        return {room.id: room for room in rooms}

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

    async def lock_live_project_id(self, chatroom_id: uuid.UUID) -> uuid.UUID | None:
        """Return a live room's project while blocking concurrent soft deletes.

        The caller must keep its transaction open until every room-scoped side
        effect has been emitted. ``FOR SHARE`` conflicts with the update used by
        both chatroom and workspace soft deletion, so neither can commit between
        this live-state check and publication.
        """
        statement = (
            sa.select(t.workspaces.c.project_id)
            .select_from(t.chatrooms.join(t.workspaces, t.chatrooms.c.workspace_id == t.workspaces.c.id))
            .where(
                sa.and_(
                    t.chatrooms.c.id == chatroom_id,
                    t.chatrooms.c.deleted_at.is_(None),
                    t.workspaces.c.deleted_at.is_(None),
                )
            )
            .with_for_update(read=True, of=[t.chatrooms.c.id, t.workspaces.c.id])
        )
        row = (await self._db.execute(statement)).first()
        return row.project_id if row else None

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

    async def restore(self, chatroom_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            t.chatrooms.update()
            .where(
                sa.and_(
                    t.chatrooms.c.id == chatroom_id,
                    t.chatrooms.c.deleted_at.isnot(None),
                )
            )
            .values(deleted_at=None)
        )
        return result.rowcount > 0


class ChatroomAgentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        role: ChatroomAgentRole = ChatroomAgentRole.NORMAL,
    ) -> None:
        # on_conflict_do_nothing: re-adding an existing binding never
        # overwrites its role (role changes go through set_role only).
        stmt = (
            pg.insert(t.chatroom_agents)
            .values(
                chatroom_id=chatroom_id,
                agent_id=agent_id,
                role=role.value,
            )
            .on_conflict_do_nothing()
        )
        await self._db.execute(stmt)

    async def remove(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        only_role: ChatroomAgentRole | None = None,
    ) -> bool:
        """Delete a binding, returning whether a row was actually removed.

        ``only_role`` scopes the delete to a single role in the same statement
        (O-5): a non-creator unbind targets normal bindings only, so an
        observer binding is an atomic no-op — no separate role read that a
        concurrent promotion could race, and no 403-vs-204 response difference
        that would out a hidden observer (R28.09/R28.10)."""
        predicate = sa.and_(
            t.chatroom_agents.c.chatroom_id == chatroom_id,
            t.chatroom_agents.c.agent_id == agent_id,
        )
        if only_role is not None:
            predicate = sa.and_(predicate, t.chatroom_agents.c.role == only_role.value)
        result = await self._db.execute(t.chatroom_agents.delete().where(predicate))
        return result.rowcount > 0

    async def list(self, chatroom_id: uuid.UUID) -> Sequence[ChatroomAgent]:
        rows = (
            await self._db.execute(
                t.chatroom_agents.select().where(
                    t.chatroom_agents.c.chatroom_id == chatroom_id,
                )
            )
        ).all()
        return [
            ChatroomAgent(
                chatroom_id=r.chatroom_id,
                agent_id=r.agent_id,
                role=ChatroomAgentRole(r.role),
                may_control_activities=bool(r.may_control_activities),
                activity_type_allowlist=_allowlist_from_json(r.activity_type_allowlist),
                granted_by_user_id=r.granted_by_user_id,
            )
            for r in rows
        ]

    async def shared_room_by_agent(self, agent_id: uuid.UUID) -> dict[uuid.UUID, uuid.UUID]:
        """For each agent sharing at least one live chatroom with ``agent_id``,
        one such shared chatroom id.

        Used by A2A broadcast (G.2) to grant rule 3a to room-mates: the shared
        room is the invocation context both are attached to. Excludes the agent
        itself and soft-deleted rooms.
        """
        ca1 = t.chatroom_agents.alias("ca1")
        ca2 = t.chatroom_agents.alias("ca2")
        # DISTINCT ON picks one shared room per room-mate (Postgres has no
        # min(uuid)); the specific room does not matter, only that one exists.
        rows = (
            await self._db.execute(
                sa.select(ca2.c.agent_id, ca1.c.chatroom_id.label("room"))
                .select_from(
                    ca1.join(ca2, ca1.c.chatroom_id == ca2.c.chatroom_id).join(
                        t.chatrooms, t.chatrooms.c.id == ca1.c.chatroom_id
                    )
                )
                .where(
                    ca1.c.agent_id == agent_id,
                    ca2.c.agent_id != agent_id,
                    t.chatrooms.c.deleted_at.is_(None),
                )
                .distinct(ca2.c.agent_id)
                .order_by(ca2.c.agent_id, ca1.c.chatroom_id)
            )
        ).all()
        return {r.agent_id: r.room for r in rows}

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

    async def role_of(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> ChatroomAgentRole | None:
        row = (
            await self._db.execute(
                sa.select(t.chatroom_agents.c.role).where(
                    sa.and_(
                        t.chatroom_agents.c.chatroom_id == chatroom_id,
                        t.chatroom_agents.c.agent_id == agent_id,
                    )
                )
            )
        ).first()
        return ChatroomAgentRole(row.role) if row else None

    async def set_role(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        expected_role: ChatroomAgentRole,
        role: ChatroomAgentRole,
    ) -> bool:
        """CAS on ``expected_role``: the UPDATE only matches if the row's role
        is still what the caller observed, so two concurrent role-change
        requests racing off the same stale read can't both report having
        performed the transition (which would otherwise double-log an
        inaccurate ``old_role`` in the audit trail — see chatroom_service.py::
        set_agent_role)."""
        result = await self._db.execute(
            t.chatroom_agents.update()
            .where(
                sa.and_(
                    t.chatroom_agents.c.chatroom_id == chatroom_id,
                    t.chatroom_agents.c.agent_id == agent_id,
                    t.chatroom_agents.c.role == expected_role.value,
                )
            )
            .values(role=role.value)
        )
        return bool(result.rowcount)

    async def set_activity_grant(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        granted: bool,
        activity_type_ids: Sequence[uuid.UUID],
        granted_by_user_id: uuid.UUID,
    ) -> bool:
        """Write one binding's delegated activity grant ([R30.37]).

        Returns whether a binding was actually updated, so the caller can answer 404
        for an agent that is not bound to this room rather than reporting a grant it
        never wrote.

        A revoke clears neither the allowlist nor the grantor: keeping them preserves
        the teacher's selection across a revoke and re-grant, and every read path
        gates on ``may_control_activities`` first, so the residue is a remembered
        setting rather than latent authority. The DB CHECKs permit exactly this
        state and refuse its two inverses (0078).
        """
        values: dict[str, Any] = {"may_control_activities": granted}
        if granted:
            # Deduplicated on the way in, order preserved. The route dedupes only to
            # bound its validation loop, so without this a direct API call repeating
            # an id would store it twice — costing a repeated resolution on every
            # turn, and leaving the settings panel permanently "dirty" because its
            # draft holds each id once.
            values["activity_type_allowlist"] = [str(i) for i in dict.fromkeys(activity_type_ids)]
            values["granted_by_user_id"] = granted_by_user_id
        result = await self._db.execute(
            t.chatroom_agents.update()
            .where(
                sa.and_(
                    t.chatroom_agents.c.chatroom_id == chatroom_id,
                    t.chatroom_agents.c.agent_id == agent_id,
                )
            )
            .values(**values)
        )
        return bool(result.rowcount)

    async def activity_control_grant(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> ActivityControlGrant | None:
        """The live grant for one binding, or ``None`` ([R30.37]).

        ``None`` covers every non-granted case uniformly: no binding, the switch
        off, or a row whose grantor is gone. A caller may therefore treat a returned
        grant as authorization without re-checking anything, and the turn engine's
        fail-closed posture is a plain ``if grant is None``.

        **The null-grantor arm is load-bearing, not defensive.** It is the only thing
        enforcing "a grant that cannot name the person answerable for it confers
        nothing" — 0078 deliberately ships no CHECK for that, because
        ``granted_by_user_id`` is ``ON DELETE SET NULL`` and such a constraint would
        abort an admin's GDPR hard-delete of any user who had ever granted activity
        control, with no way to clear the grant first. Deleting the granter makes the
        grant inert here instead. Do not "simplify" this branch away.
        """
        row = (
            await self._db.execute(
                sa.select(
                    t.chatroom_agents.c.may_control_activities,
                    t.chatroom_agents.c.activity_type_allowlist,
                    t.chatroom_agents.c.granted_by_user_id,
                ).where(
                    sa.and_(
                        t.chatroom_agents.c.chatroom_id == chatroom_id,
                        t.chatroom_agents.c.agent_id == agent_id,
                    )
                )
            )
        ).first()
        if row is None or not row.may_control_activities or row.granted_by_user_id is None:
            return None
        return ActivityControlGrant(
            agent_id=agent_id,
            granted_by_user_id=row.granted_by_user_id,
            activity_type_ids=_allowlist_from_json(row.activity_type_allowlist),
        )

    async def rooms_with_observers(
        self,
        chatroom_ids: Sequence[uuid.UUID],
    ) -> set[uuid.UUID]:
        """Which of *chatroom_ids* have at least one observer binding.

        One query for a whole room list, so DTO mapping stays O(1) queries.
        """
        if not chatroom_ids:
            return set()
        rows = (
            await self._db.execute(
                sa.select(t.chatroom_agents.c.chatroom_id)
                .distinct()
                .where(
                    sa.and_(
                        t.chatroom_agents.c.chatroom_id.in_(list(chatroom_ids)),
                        t.chatroom_agents.c.role == ChatroomAgentRole.OBSERVER.value,
                    )
                )
            )
        ).all()
        return {r.chatroom_id for r in rows}

    async def list_live_bindings(
        self,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> list[tuple[uuid.UUID, uuid.UUID, ChatroomAgentRole]]:  # type: ignore[valid-type]
        """Return (agent_id, chatroom_id, role) triples for non-deleted chatrooms.

        Used by the silence-trigger sweep (M19) so the SQL join stays in the
        repository instead of the worker task. The role rides along so the
        sweep can exempt observer bindings from the presence gate (O-2/R28.04)
        without a per-pair role lookup.
        """
        rows = (
            await self._db.execute(
                sa.select(
                    t.chatroom_agents.c.agent_id,
                    t.chatroom_agents.c.chatroom_id,
                    t.chatroom_agents.c.role,
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
        return [(r.agent_id, r.chatroom_id, ChatroomAgentRole(r.role)) for r in rows]


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
