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
from contexts.agent_groups.infrastructure import tables as t
from shared_kernel.auth.clients import now


class AgentGroupRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_group(self, *, project_id: uuid.UUID, name: str) -> uuid.UUID:
        """Insert a group and return its id (caller owns commit).

        The ``uq_agent_groups_project_name_active`` partial-unique makes a
        duplicate active name in the project a domain 409, not a 500.
        """
        try:
            row = await self._db.execute(
                t.agent_groups.insert()
                .values(project_id=project_id, name=name)
                .returning(t.agent_groups.c.id)
            )
        except IntegrityError as exc:
            raise AgentGroupNameConflict(
                f"group name {name!r} already exists in project {project_id}"
            ) from exc
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
        return bool(result.rowcount)

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
        return bool(result.rowcount)

    async def add_member(self, *, group_id: uuid.UUID, agent_id: uuid.UUID) -> None:
        """Add an agent to a group; idempotent on the (group, agent) PK.

        ``ON CONFLICT DO NOTHING`` so re-adding an existing member is a no-op
        rather than an IntegrityError — the caller need not pre-check membership.
        """
        await self._db.execute(
            pg_insert(t.agent_group_members)
            .values(agent_group_id=group_id, agent_id=agent_id)
            .on_conflict_do_nothing(index_elements=["agent_group_id", "agent_id"])
        )

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
        return bool(result.rowcount)

    async def list_member_agent_ids(self, group_id: uuid.UUID) -> Sequence[uuid.UUID]:
        """Live member agent ids for a group, ordered for deterministic feeds.

        Read fresh on every call (never cached) so a removed member loses build
        ingestion and, via the WS4 resolver, retrieval access on the next turn.
        """
        rows = (
            await self._db.execute(
                sa.select(t.agent_group_members.c.agent_id)
                .where(t.agent_group_members.c.agent_group_id == group_id)
                .order_by(t.agent_group_members.c.agent_id)
            )
        ).all()
        return [r.agent_id for r in rows]


__all__ = ["AgentGroupRepository"]
