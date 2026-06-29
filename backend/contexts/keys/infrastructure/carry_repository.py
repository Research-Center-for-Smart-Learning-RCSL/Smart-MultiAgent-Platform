"""Repository for `key_projects` (D.5)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.domain.models import ApiKey
from contexts.keys.domain.providers import ApiKeyProvider
from contexts.keys.infrastructure import tables as t
from contexts.keys.infrastructure.probes.base import ProbeStatus


@dataclass(frozen=True, slots=True)
class Carry:
    key_id: uuid.UUID
    project_id: uuid.UUID
    carried: bool
    added_by_user_id: uuid.UUID | None
    added_at: datetime
    withdrawn_at: datetime | None


@dataclass(frozen=True, slots=True)
class KeyProjectUsage:
    """Reverse view of a single key's footprint in one project.

    `group_ids` is every *active* Key Group inside `project_id` that lists the
    key as a member — empty when the key is merely carried but not yet wired
    into any group. The agents-side binding count is resolved separately
    (cross-context) by the caller via the agents facade.
    """

    project_id: uuid.UUID
    carried_at: datetime
    group_ids: tuple[uuid.UUID, ...]


class KeyProjectRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def upsert_carry(
        self,
        *,
        key_id: uuid.UUID,
        project_id: uuid.UUID,
        added_by_user_id: uuid.UUID,
    ) -> None:
        """Add or re-activate a carry row.

        Re-carry of a previously withdrawn row flips `carried=true` and clears
        `withdrawn_at`. `added_by_user_id` / `added_at` are preserved on
        re-carry so the audit trail keeps the original attribution — users
        can see "originally added by X on Y" even after a withdraw/re-carry
        cycle.
        """
        stmt = (
            pg_insert(t.key_projects)
            .values(
                key_id=key_id,
                project_id=project_id,
                carried=True,
                added_by_user_id=added_by_user_id,
            )
            .on_conflict_do_update(
                index_elements=["key_id", "project_id"],
                set_={"carried": True, "withdrawn_at": None},
            )
        )
        await self._db.execute(stmt)

    async def withdraw(self, *, key_id: uuid.UUID, project_id: uuid.UUID, at: datetime) -> bool:
        """Soft-withdraw. Returns True if a row was actually flipped."""
        result = await self._db.execute(
            t.key_projects.update()
            .where(
                sa.and_(
                    t.key_projects.c.key_id == key_id,
                    t.key_projects.c.project_id == project_id,
                    t.key_projects.c.carried.is_(True),
                )
            )
            .values(carried=False, withdrawn_at=at)
        )
        return bool(result.rowcount)

    async def withdraw_all_for_key(self, *, key_id: uuid.UUID, at: datetime) -> int:
        """Withdraw every active carry for *key_id* across all projects."""
        result = await self._db.execute(
            t.key_projects.update()
            .where(
                sa.and_(
                    t.key_projects.c.key_id == key_id,
                    t.key_projects.c.carried.is_(True),
                )
            )
            .values(carried=False, withdrawn_at=at)
        )
        return result.rowcount or 0

    async def list_active_in_project(self, project_id: uuid.UUID) -> list[ApiKey]:
        """Return the `ApiKey` projection for every key currently carried in."""
        stmt = (
            sa.select(t.api_keys)
            .select_from(t.api_keys.join(t.key_projects, t.api_keys.c.id == t.key_projects.c.key_id))
            .where(
                sa.and_(
                    t.key_projects.c.project_id == project_id,
                    t.key_projects.c.carried.is_(True),
                    t.api_keys.c.deleted_at.is_(None),
                )
            )
            .order_by(t.api_keys.c.created_at.desc())
        )
        rows = (await self._db.execute(stmt)).all()
        return [_row_to_apikey(r) for r in rows]

    async def list_projects_for_key(self, key_id: uuid.UUID) -> list[KeyProjectUsage]:
        """Every project `key_id` is actively carried into, plus the active Key
        Groups in each project that contain the key.

        One left-join walk so a carried-but-ungrouped project still surfaces
        (with an empty `group_ids`). Group membership in *other* projects is
        excluded by pinning ``key_groups.project_id = key_projects.project_id``.
        """
        kp = t.key_projects
        kgm = t.key_group_members
        kg = t.key_groups
        stmt = (
            sa.select(kp.c.project_id, kp.c.added_at, kg.c.id.label("group_id"))
            .select_from(
                kp.outerjoin(kgm, kgm.c.key_id == kp.c.key_id).outerjoin(
                    kg,
                    sa.and_(
                        kg.c.id == kgm.c.group_id,
                        kg.c.project_id == kp.c.project_id,
                        kg.c.deleted_at.is_(None),
                    ),
                )
            )
            .where(sa.and_(kp.c.key_id == key_id, kp.c.carried.is_(True)))
            .order_by(kp.c.added_at.desc())
        )
        rows = (await self._db.execute(stmt)).all()
        ordered: list[uuid.UUID] = []
        carried_at: dict[uuid.UUID, datetime] = {}
        groups: dict[uuid.UUID, list[uuid.UUID]] = {}
        for row in rows:
            pid = row.project_id
            if pid not in groups:
                ordered.append(pid)
                groups[pid] = []
                carried_at[pid] = row.added_at
            if row.group_id is not None and row.group_id not in groups[pid]:
                groups[pid].append(row.group_id)
        return [
            KeyProjectUsage(project_id=pid, carried_at=carried_at[pid], group_ids=tuple(groups[pid]))
            for pid in ordered
        ]

    async def count_active_by_keys(self, key_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Active-carry project count per key (for the my-keys list badge)."""
        if not key_ids:
            return {}
        kp = t.key_projects
        stmt = (
            sa.select(kp.c.key_id, sa.func.count().label("n"))
            .where(sa.and_(kp.c.key_id.in_(key_ids), kp.c.carried.is_(True)))
            .group_by(kp.c.key_id)
        )
        rows = (await self._db.execute(stmt)).all()
        return {row.key_id: int(row.n) for row in rows}

    async def list_active_carries_for_user(
        self, *, user_id: uuid.UUID, project_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Return every currently-carried `key_id` whose key is owned by user.

        Used by the membership-removal fanout (R7.04): when a user leaves or
        is removed from a project, we need to withdraw every key of theirs
        that they carried in.
        """
        stmt = (
            sa.select(t.key_projects.c.key_id)
            .select_from(t.key_projects.join(t.api_keys, t.api_keys.c.id == t.key_projects.c.key_id))
            .where(
                sa.and_(
                    t.key_projects.c.project_id == project_id,
                    t.key_projects.c.carried.is_(True),
                    t.api_keys.c.owner_user_id == user_id,
                )
            )
        )
        return [row.key_id for row in (await self._db.execute(stmt)).all()]


def _row_to_apikey(row: Any) -> ApiKey:
    return ApiKey(
        id=row.id,
        owner_user_id=row.owner_user_id,
        provider=ApiKeyProvider(row.provider),
        name=row.name,
        masked_preview=row.masked_preview,
        test_status=ProbeStatus(row.test_status),
        test_error=row.test_error,
        last_test_at=row.last_test_at,
        transit_key_version=row.transit_key_version,
        hmac_key_version=row.hmac_key_version,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


__all__ = ["Carry", "KeyProjectRepository", "KeyProjectUsage"]
