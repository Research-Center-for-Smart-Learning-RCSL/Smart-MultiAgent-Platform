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
    DraftReadGrant,
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
        allow_member_groups=row.allow_member_groups,
        guest_token=row.guest_token,
        version=row.version,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
        created_by_user_id=row.created_by_user_id,
        disclose_observers=row.disclose_observers,
        disclose_drafts=row.disclose_drafts,
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
        allow_member_groups: bool = False,
        created_by_user_id: uuid.UUID | None = None,
    ) -> Chatroom:
        token = _new_guest_token()
        row = (
            await self._db.execute(
                t.chatrooms.insert()
                .values(
                    workspace_id=workspace_id,
                    name=name,
                    allow_member_groups=allow_member_groups,
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

    async def list_candidates(
        self,
        *,
        workspace_ids: Sequence[uuid.UUID] | None = None,
        project_ids: Sequence[uuid.UUID] | None = None,
        limit: int,
    ) -> tuple[list[tuple[uuid.UUID, Chatroom]], bool]:
        """Live rooms under the given workspaces *or* projects, each with its
        parent project id, for the visibility-filtered listings (R13.32).

        Returns ``(rows, truncated)``. One extra row beyond `limit` is fetched so
        truncation is detected rather than inferred from a full page; the caller
        decides what to say about it. Silent truncation is the failure mode this
        shape exists to prevent — a listing that quietly stops reads as "that is
        all there is".

        Ordering is ``(created_at, id)``. `created_at` alone is not a total order,
        and the caller paginates *after* filtering, so a tie that reorders between
        two requests would drop or duplicate a room across page boundaries.

        Exactly one of `workspace_ids` / `project_ids` must be given: they select
        different scopes and passing both would silently union them.
        """
        if workspace_ids is not None and project_ids is None:
            scope_predicate: sa.ColumnElement[bool] = t.workspaces.c.id.in_(list(workspace_ids))
            empty = not workspace_ids
        elif project_ids is not None and workspace_ids is None:
            scope_predicate = t.workspaces.c.project_id.in_(list(project_ids))
            empty = not project_ids
        else:
            raise ValueError("pass exactly one of workspace_ids / project_ids")
        if empty:
            return [], False

        # The third join is for `projects.deleted_at` alone. `resolve_room_access`
        # refuses a room whose project is soft-deleted — it raises
        # `ChatroomNotFound` once `get_project` returns None — while the role
        # resolver reads projects with `include_deleted=True`. Without this filter
        # a member of a deleted project is handed its rooms by the listing and then
        # 404s on open: the listable-but-unopenable divergence this predicate
        # exists to prevent. Referenced by FK only, exactly as
        # `workspaces.project_id` already is; no tenancy semantics are read here.
        projects = sa.table("projects", sa.column("id"), sa.column("deleted_at"))
        stmt = (
            sa.select(t.chatrooms, t.workspaces.c.project_id.label("parent_project_id"))
            .select_from(
                t.chatrooms.join(t.workspaces, t.chatrooms.c.workspace_id == t.workspaces.c.id).join(
                    projects, projects.c.id == t.workspaces.c.project_id
                )
            )
            .where(
                sa.and_(
                    scope_predicate,
                    t.chatrooms.c.deleted_at.is_(None),
                    t.workspaces.c.deleted_at.is_(None),
                    projects.c.deleted_at.is_(None),
                )
            )
            .order_by(t.chatrooms.c.created_at, t.chatrooms.c.id)
            .limit(limit + 1)
        )
        rows = (await self._db.execute(stmt)).all()
        truncated = len(rows) > limit
        return [(r.parent_project_id, _row_to_chatroom(r)) for r in rows[:limit]], truncated

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
                may_read_drafts=bool(r.may_read_drafts),
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

    async def set_draft_grant(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        granted: bool,
        granted_by_user_id: uuid.UUID,
    ) -> bool:
        """Write one binding's live-draft reading grant ([R32.03]).

        Returns whether a binding was actually updated, so the caller can answer 404
        for an agent that is not bound here rather than reporting a grant it never
        wrote — the same contract :meth:`set_activity_grant` has.

        **A revoke clears the grantor too**, which is where this deliberately differs
        from its sibling. There, the residue is a remembered *selection* (which
        worksheets the teacher picked) worth preserving across a re-grant; here there
        is no selection, so the only thing a retained grantor could do is keep a
        person named as answerable for an authority nobody holds. It is cleared only
        when the *other* grant is also off: the column is shared (0082), and clearing
        it under a live activity grant would silently make that grant inert.
        """
        values: dict[str, Any] = {"may_read_drafts": granted}
        if granted:
            values["granted_by_user_id"] = granted_by_user_id
        where = sa.and_(
            t.chatroom_agents.c.chatroom_id == chatroom_id,
            t.chatroom_agents.c.agent_id == agent_id,
        )
        result = await self._db.execute(t.chatroom_agents.update().where(where).values(**values))
        if not result.rowcount:
            return False
        if not granted:
            # Second statement rather than a CASE in the first: the condition is on
            # the row's *other* grant, and expressing "clear the grantor only if
            # may_control_activities is false" inline would read as though the two
            # grants were one thing. They are not, and a later reader untangling a
            # CASE is exactly how the shared column becomes a bug.
            await self._db.execute(
                t.chatroom_agents.update()
                .where(sa.and_(where, t.chatroom_agents.c.may_control_activities.is_(False)))
                .values(granted_by_user_id=None)
            )
        return True

    async def draft_read_grant(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> DraftReadGrant | None:
        """The live draft-reading grant for one binding, or ``None`` ([R32.03]).

        ``None`` covers every non-granted case uniformly — no binding, the switch
        off, or a row whose grantor is gone — so the runtime's fail-closed posture
        stays a plain ``if grant is None``.

        The null-grantor arm is load-bearing for the reason
        :meth:`activity_control_grant` states at length: 0082 ships no CHECK for it
        because the column is ``ON DELETE SET NULL`` and such a constraint would
        abort an admin's GDPR hard-delete of anyone who had ever granted this. A
        deleted granter makes the grant inert here instead. Do not simplify it away.
        """
        row = (
            await self._db.execute(
                sa.select(
                    t.chatroom_agents.c.may_read_drafts,
                    t.chatroom_agents.c.granted_by_user_id,
                ).where(
                    sa.and_(
                        t.chatroom_agents.c.chatroom_id == chatroom_id,
                        t.chatroom_agents.c.agent_id == agent_id,
                    )
                )
            )
        ).first()
        if row is None or not row.may_read_drafts or row.granted_by_user_id is None:
            return None
        return DraftReadGrant(agent_id=agent_id, granted_by_user_id=row.granted_by_user_id)

    async def room_has_draft_reader(self, chatroom_id: uuid.UUID) -> bool:
        """Does any binding in this room hold a live draft grant? ([R32.03])

        Two callers, one question. The WS handler asks it to decide whether to store
        anything at all — a room nobody may read stores nothing — and the chatroom
        DTO asks it to drive the disclosure chip. Both need the *room's* answer
        rather than one binding's, and neither may infer it from a listing that a
        non-creator is not allowed to see.

        ``granted_by_user_id`` is checked here too, so this agrees with
        :meth:`draft_read_grant` by construction: a room whose only granted binding
        has a deleted granter offers no tool, and must therefore also store no
        drafts and show no chip. Deriving one from `may_read_drafts` alone would
        make the chip claim a reader that does not exist.
        """
        row = (
            await self._db.execute(
                sa.select(sa.literal(1))
                .select_from(t.chatroom_agents)
                .where(
                    sa.and_(
                        t.chatroom_agents.c.chatroom_id == chatroom_id,
                        t.chatroom_agents.c.may_read_drafts.is_(True),
                        t.chatroom_agents.c.granted_by_user_id.isnot(None),
                    )
                )
                .limit(1)
            )
        ).first()
        return row is not None

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


class ChatroomMemberGroupRepository:
    """Which Member Groups a room is bound to (§13.2a, R13.29).

    Reads return raw binding rows, live and stale alike: a binding whose group was
    soft-deleted is not filtered here, and does not need to be. The room ACL
    intersects these ids with the caller's *live* group ids, so a dead group can
    never match anybody. Filtering here as well would mean this context reading the
    tenancy context's `deleted_at`, which is exactly the cross-context table read
    the boundary exists to prevent.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_for_room(self, chatroom_id: uuid.UUID) -> set[uuid.UUID]:
        rows = (
            await self._db.execute(
                sa.select(t.chatroom_member_groups.c.member_group_id).where(
                    t.chatroom_member_groups.c.chatroom_id == chatroom_id
                )
            )
        ).all()
        return {r.member_group_id for r in rows}

    async def bound_group_ids(self, chatroom_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, set[uuid.UUID]]:
        """Bindings for many rooms in one query, keyed by room id.

        Rooms with no binding are absent from the mapping rather than mapped to an
        empty set; callers reading it with `.get(room_id, frozenset())` treat the
        two identically, and the sparse form keeps a listing of mostly-unbound rooms
        from allocating a set per room.
        """
        if not chatroom_ids:
            return {}
        rows = (
            await self._db.execute(
                t.chatroom_member_groups.select().where(
                    t.chatroom_member_groups.c.chatroom_id.in_(list(chatroom_ids))
                )
            )
        ).all()
        out: dict[uuid.UUID, set[uuid.UUID]] = {}
        for row in rows:
            out.setdefault(row.chatroom_id, set()).add(row.member_group_id)
        return out

    async def replace(self, *, chatroom_id: uuid.UUID, group_ids: Sequence[uuid.UUID]) -> None:
        """Set the room's bindings to exactly `group_ids`.

        Replace rather than add/remove: the settings UI edits the whole set, and a
        partial API would let two concurrent editors each apply half of their intent
        and leave a set neither of them chose.
        """
        await self._db.execute(
            t.chatroom_member_groups.delete().where(t.chatroom_member_groups.c.chatroom_id == chatroom_id)
        )
        unique = list(dict.fromkeys(group_ids))
        if unique:
            await self._db.execute(
                t.chatroom_member_groups.insert(),
                [{"chatroom_id": chatroom_id, "member_group_id": gid} for gid in unique],
            )


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

    async def guest_room_ids(
        self,
        *,
        user_id: uuid.UUID,
        chatroom_ids: Sequence[uuid.UUID],
    ) -> set[uuid.UUID]:
        """Of `chatroom_ids`, the subset the user holds a guest row in (one query).

        The batch form of :meth:`is_guest`, for the listing paths that must
        evaluate the room flags over many rooms at once and cannot afford a
        per-room round trip.
        """
        if not chatroom_ids:
            return set()
        rows = (
            await self._db.execute(
                sa.select(t.chatroom_guests.c.chatroom_id).where(
                    sa.and_(
                        t.chatroom_guests.c.user_id == user_id,
                        t.chatroom_guests.c.chatroom_id.in_(list(chatroom_ids)),
                    )
                )
            )
        ).all()
        return {r.chatroom_id for r in rows}

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
