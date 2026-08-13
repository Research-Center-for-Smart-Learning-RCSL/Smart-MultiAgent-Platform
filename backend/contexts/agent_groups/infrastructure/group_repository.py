"""Async repository for ``agent_groups`` + ``agent_group_members`` (R24.06).

Phase 1 created agent_groups as internal singletons (one member = the former
owning agent), managed inline by the GraphRAG config service. Phase 2b WS1
makes a group multi-member, so member management moves here — a dedicated
repository mirroring ``keys/infrastructure/group_repository.py`` for the member
add/remove/list surface the GraphRAG owner-centric create (WS2), the build
delta-feed (WS1), and the layered resolver (WS4) share.

All writes keep the caller's :class:`AsyncSession` transaction; the service /
worker layer owns commit so audit rows and membership stay atomic.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agent_groups.domain.errors import AgentGroupNameConflict
from contexts.agent_groups.domain.models import AgentGroup
from contexts.agent_groups.infrastructure import tables as t
from contexts.agents.infrastructure import tables as agents_t
from shared_kernel.auth.clients import now
from shared_kernel.db.rowcount import rowcount


def _row_to_group(row: object) -> AgentGroup:
    return AgentGroup(
        id=row.id,  # type: ignore[attr-defined]
        project_id=row.project_id,  # type: ignore[attr-defined]
        name=row.name,  # type: ignore[attr-defined]
        concept_map_enabled=row.concept_map_enabled,  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
    )


_GROUP_COLS = (
    t.agent_groups.c.id,
    t.agent_groups.c.project_id,
    t.agent_groups.c.name,
    t.agent_groups.c.concept_map_enabled,
    t.agent_groups.c.created_at,
)


class AgentGroupRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_group(self, *, project_id: uuid.UUID, name: str) -> uuid.UUID:
        """Insert a group and return its id (caller owns commit).

        The ``uq_agent_groups_project_name_active`` partial-unique makes a
        duplicate active name in the project a domain 409, not a 500. Any
        *other* IntegrityError (e.g. a FK violation if ``project_id`` was
        deleted between the caller's own check and this insert) must not be
        mismapped to the same "name conflict" — re-raised as-is so it
        surfaces as its real cause instead of a misleading 409.
        """
        try:
            row = await self._db.execute(
                t.agent_groups.insert()
                .values(project_id=project_id, name=name)
                .returning(t.agent_groups.c.id)
            )
        except IntegrityError as exc:
            msg = str(exc.orig or exc).lower()
            if "uq_agent_groups_project_name_active" in msg:
                raise AgentGroupNameConflict(
                    f"group name {name!r} already exists in project {project_id}"
                ) from exc
            raise
        return uuid.UUID(str(row.scalar_one()))

    async def project_id_of(self, group_id: uuid.UUID) -> uuid.UUID | None:
        """Return the group's project id, or ``None`` if missing/soft-deleted.

        The authorization seam: a caller resolves the owning project here, then
        checks Project-Owner authority against it via the tenancy facade.
        """
        row = (
            await self._db.execute(
                sa.select(t.agent_groups.c.project_id).where(
                    sa.and_(
                        t.agent_groups.c.id == group_id,
                        t.agent_groups.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return row.project_id if row is not None else None

    async def list_for_project(self, project_id: uuid.UUID) -> Sequence[AgentGroup]:
        """Live groups in a project, newest first, for the list view (Phase 4α)."""
        rows = (
            await self._db.execute(
                sa.select(*_GROUP_COLS)
                .where(
                    sa.and_(
                        t.agent_groups.c.project_id == project_id,
                        t.agent_groups.c.deleted_at.is_(None),
                    )
                )
                # id as a stable tiebreak: groups created in one transaction share
                # now() (the transaction timestamp), so created_at alone would let
                # them shuffle between paginated pages.
                .order_by(t.agent_groups.c.created_at.desc(), t.agent_groups.c.id.desc())
            )
        ).all()
        return [_row_to_group(r) for r in rows]

    async def get(self, group_id: uuid.UUID) -> AgentGroup | None:
        """A single live group, or ``None`` if missing/soft-deleted (Phase 4α)."""
        row = (
            await self._db.execute(
                sa.select(*_GROUP_COLS).where(
                    sa.and_(
                        t.agent_groups.c.id == group_id,
                        t.agent_groups.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_group(row) if row is not None else None

    async def rename(self, *, group_id: uuid.UUID, name: str) -> bool:
        """Rename a live group; returns whether a live row was updated (Phase 4α).

        The ``uq_agent_groups_project_name_active`` partial-unique makes a collision
        with another active group's name a domain 409, not a 500. The
        ``deleted_at IS NULL`` guard makes a rename racing a concurrent soft-delete a
        no-op (0 rows) rather than a write to a tombstoned group.
        """
        try:
            result = await self._db.execute(
                t.agent_groups.update()
                .where(
                    sa.and_(
                        t.agent_groups.c.id == group_id,
                        t.agent_groups.c.deleted_at.is_(None),
                    )
                )
                .values(name=name)
            )
        except IntegrityError as exc:
            raise AgentGroupNameConflict(f"group name {name!r} already exists in this project") from exc
        return bool(rowcount(result))

    async def set_concept_map_enabled(self, *, group_id: uuid.UUID, enabled: bool) -> bool:
        """Toggle the group's Concept Map privacy opt-in (Phase 2b WS3, R11.10).

        Returns whether a live row was updated. The ``deleted_at IS NULL`` guard
        makes a concurrent soft-delete between the caller's existence check and
        this write a no-op (0 rows) rather than a write to a tombstoned owner.
        """
        result = await self._db.execute(
            t.agent_groups.update()
            .where(
                sa.and_(
                    t.agent_groups.c.id == group_id,
                    t.agent_groups.c.deleted_at.is_(None),
                )
            )
            .values(concept_map_enabled=enabled)
        )
        return bool(rowcount(result))

    async def soft_delete(self, *, group_id: uuid.UUID) -> bool:
        """Tombstone a group; returns whether a live row was updated (WS6).

        The ``deleted_at IS NULL`` guard makes a double-delete (or a race with a
        concurrent delete) a no-op (0 rows) rather than re-stamping a tombstoned
        row. Member rows are left in place: every read path already filters on
        ``agent_groups.deleted_at IS NULL`` (project resolve, membership feed, the
        layered resolver), so the tombstone makes the whole group inert without a
        second cascade delete of ``agent_group_members``.
        """
        result = await self._db.execute(
            t.agent_groups.update()
            .where(
                sa.and_(
                    t.agent_groups.c.id == group_id,
                    t.agent_groups.c.deleted_at.is_(None),
                )
            )
            .values(deleted_at=now())
        )
        return bool(rowcount(result))

    async def add_member(self, *, group_id: uuid.UUID, agent_id: uuid.UUID) -> bool:
        """Add an agent to a group; returns whether a row was actually inserted.

        ``ON CONFLICT DO NOTHING`` so re-adding an existing member is a no-op
        rather than an IntegrityError — the caller need not pre-check membership.

        The return value exists so the service can keep its audit event truthful:
        a caller that adds a whole set of agents, some of them already members,
        would otherwise record `member_added` for memberships that already
        existed. Mirrors ``remove_member``'s did-anything-happen contract.
        """
        result = await self._db.execute(
            pg_insert(t.agent_group_members)
            .values(agent_group_id=group_id, agent_id=agent_id)
            .on_conflict_do_nothing(index_elements=["agent_group_id", "agent_id"])
        )
        return bool(rowcount(result))

    async def remove_member(self, *, group_id: uuid.UUID, agent_id: uuid.UUID) -> bool:
        """Remove an agent from a group; returns whether a row was deleted."""
        result = await self._db.execute(
            t.agent_group_members.delete().where(
                sa.and_(
                    t.agent_group_members.c.agent_group_id == group_id,
                    t.agent_group_members.c.agent_id == agent_id,
                )
            )
        )
        return bool(rowcount(result))

    async def list_member_agent_ids(self, group_id: uuid.UUID) -> Sequence[uuid.UUID]:
        """Live member agent ids for a group, ordered for deterministic feeds.

        Read fresh on every call (never cached) so a removed member loses build
        ingestion and, via the WS4 resolver, retrieval access on the next turn.
        Joined against `agents` and filtered on `deleted_at IS NULL`: a
        soft-deleted agent's membership row is never cleaned up (soft_delete
        only stamps `agents.deleted_at`), so without this filter a deleted
        agent's chatroom history would keep being pulled into the group's
        Concept Map build scope indefinitely.
        """
        rows = (
            await self._db.execute(
                sa.select(t.agent_group_members.c.agent_id)
                .select_from(
                    t.agent_group_members.join(
                        agents_t.agents,
                        agents_t.agents.c.id == t.agent_group_members.c.agent_id,
                    )
                )
                .where(
                    sa.and_(
                        t.agent_group_members.c.agent_group_id == group_id,
                        agents_t.agents.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.agent_group_members.c.agent_id)
            )
        ).all()
        return [r.agent_id for r in rows]


__all__ = ["AgentGroupRepository"]
