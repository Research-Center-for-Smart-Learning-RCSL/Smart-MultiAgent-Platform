"""Repositories for `key_groups` + `key_group_members` (D.6)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.domain.groups import (
    HourlyLimits,
    KeyGroup,
    KeyGroupMember,
    RotationPolicy,
)
from contexts.keys.infrastructure import tables as t


def _row_to_group(row: Any) -> KeyGroup:
    return KeyGroup(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


def _row_to_member(row: Any) -> KeyGroupMember:
    return KeyGroupMember(
        group_id=row.group_id,
        key_id=row.key_id,
        priority=row.priority,
        rotation=RotationPolicy(
            rotate_on_error_codes=tuple(row.rotate_on_error_codes),
            rotate_on_token_quota=row.rotate_on_token_quota,
            retry_on_error=row.retry_on_error,
            retry_initial_delay_ms=row.retry_initial_delay_ms,
            retry_multiplier=Decimal(row.retry_multiplier),
            retry_max_delay_ms=row.retry_max_delay_ms,
            retry_max=row.retry_max,
            retry_jitter_pct=row.retry_jitter_pct,
        ),
        limits=HourlyLimits(
            max_input_tokens_per_hour=row.max_input_tokens_per_hour,
            max_output_tokens_per_hour=row.max_output_tokens_per_hour,
            max_requests_per_hour=row.max_requests_per_hour,
        ),
    )


class KeyGroupRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, *, project_id: uuid.UUID, name: str) -> KeyGroup:
        stmt = t.key_groups.insert().values(project_id=project_id, name=name).returning(t.key_groups)
        row = (await self._db.execute(stmt)).one()
        return _row_to_group(row)

    async def get_active(self, group_id: uuid.UUID) -> KeyGroup | None:
        row = (
            await self._db.execute(
                t.key_groups.select().where(
                    sa.and_(
                        t.key_groups.c.id == group_id,
                        t.key_groups.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_group(row) if row else None

    async def list_for_project_with_counts(
        self, project_id: uuid.UUID
    ) -> list[tuple[KeyGroup, int]]:
        m = t.key_group_members
        kp = t.key_projects
        ak = t.api_keys
        carried_count = (
            sa.select(sa.func.count(m.c.key_id))
            .select_from(
                m.join(
                    kp,
                    sa.and_(
                        kp.c.key_id == m.c.key_id,
                        kp.c.project_id == t.key_groups.c.project_id,
                        kp.c.carried.is_(True),
                    ),
                ).join(ak, sa.and_(ak.c.id == m.c.key_id, ak.c.deleted_at.is_(None)))
            )
            .where(m.c.group_id == t.key_groups.c.id)
            .correlate(t.key_groups)
            .scalar_subquery()
            .label("member_count")
        )
        stmt = (
            sa.select(t.key_groups, carried_count)
            .where(
                sa.and_(
                    t.key_groups.c.project_id == project_id,
                    t.key_groups.c.deleted_at.is_(None),
                )
            )
            .order_by(t.key_groups.c.created_at.desc())
        )
        rows = (await self._db.execute(stmt)).all()
        return [(_row_to_group(r), r.member_count) for r in rows]

    async def rename(self, group_id: uuid.UUID, name: str) -> None:
        await self._db.execute(t.key_groups.update().where(t.key_groups.c.id == group_id).values(name=name))

    async def soft_delete(self, group_id: uuid.UUID, *, at: Any) -> None:
        await self._db.execute(
            t.key_groups.update().where(t.key_groups.c.id == group_id).values(deleted_at=at)
        )


class KeyGroupMemberRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_ordered(self, group_id: uuid.UUID) -> list[KeyGroupMember]:
        rows = (
            await self._db.execute(
                t.key_group_members.select()
                .where(t.key_group_members.c.group_id == group_id)
                .order_by(t.key_group_members.c.priority.asc())
            )
        ).all()
        return [_row_to_member(r) for r in rows]

    async def list_ordered_carried(self, group_id: uuid.UUID) -> list[KeyGroupMember]:
        """Members whose key is *actively carried* into the group's project.

        SEC-H3: provider-call eligibility must require an active
        ``key_projects`` carry row scoped to the group's project — not bare
        group membership. A withdrawn carry (``carried=false``) — a Project
        Owner pulling a user's key, or the membership-removal fanout — drops
        the member here, so the provider router can no longer select or
        decrypt that key. Because the check is a DB join re-run on every call,
        a revocation takes effect immediately and does not depend on the
        in-process DEK cache TTL or an at-most-once pub/sub invalidation.
        """
        m = t.key_group_members
        kg = t.key_groups
        kp = t.key_projects
        stmt = (
            sa.select(m)
            .select_from(
                m.join(kg, kg.c.id == m.c.group_id).join(
                    kp,
                    sa.and_(
                        kp.c.key_id == m.c.key_id,
                        kp.c.project_id == kg.c.project_id,
                        kp.c.carried.is_(True),
                    ),
                )
            )
            .where(m.c.group_id == group_id)
            .order_by(m.c.priority.asc())
        )
        rows = (await self._db.execute(stmt)).all()
        return [_row_to_member(r) for r in rows]

    async def add(
        self,
        *,
        group_id: uuid.UUID,
        key_id: uuid.UUID,
        priority: int,
    ) -> None:
        await self._db.execute(
            t.key_group_members.insert().values(
                group_id=group_id,
                key_id=key_id,
                priority=priority,
            )
        )

    async def next_priority(self, group_id: uuid.UUID) -> int:
        row = (
            await self._db.execute(
                sa.select(sa.func.coalesce(sa.func.max(t.key_group_members.c.priority), 0)).where(
                    t.key_group_members.c.group_id == group_id
                )
            )
        ).scalar_one()
        return int(row) + 1

    async def patch(
        self,
        *,
        group_id: uuid.UUID,
        key_id: uuid.UUID,
        updates: dict[str, Any],
    ) -> None:
        if not updates:
            return
        await self._db.execute(
            t.key_group_members.update()
            .where(
                sa.and_(
                    t.key_group_members.c.group_id == group_id,
                    t.key_group_members.c.key_id == key_id,
                )
            )
            .values(**updates)
        )

    async def remove(self, *, group_id: uuid.UUID, key_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            t.key_group_members.delete().where(
                sa.and_(
                    t.key_group_members.c.group_id == group_id,
                    t.key_group_members.c.key_id == key_id,
                )
            )
        )
        return bool(result.rowcount)

    async def remove_all_for_key(self, key_id: uuid.UUID) -> int:
        """Delete every membership row referencing *key_id* across all groups."""
        result = await self._db.execute(
            t.key_group_members.delete().where(t.key_group_members.c.key_id == key_id)
        )
        return result.rowcount or 0

    async def reorder_atomic(self, *, group_id: uuid.UUID, priorities: dict[uuid.UUID, int]) -> None:
        """Bulk-update priorities inside a deferred-constraint transaction.

        The UNIQUE (group_id, priority) constraint is DEFERRABLE; we switch
        it to DEFERRED for this statement so intermediate duplicates during
        the shuffle don't error. All checks run at COMMIT, so the final
        assignment must still be unique.
        """
        if not priorities:
            return
        await self._db.execute(sa.text("SET CONSTRAINTS uq_key_group_members_priority DEFERRED"))
        for key_id, new_priority in priorities.items():
            await self._db.execute(
                t.key_group_members.update()
                .where(
                    sa.and_(
                        t.key_group_members.c.group_id == group_id,
                        t.key_group_members.c.key_id == key_id,
                    )
                )
                .values(priority=new_priority)
            )


__all__ = ["KeyGroupMemberRepository", "KeyGroupRepository"]
