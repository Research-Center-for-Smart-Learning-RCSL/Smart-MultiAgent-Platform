"""Skills repositories — SQL only, no policy (§31).

Every containment, ownership, and budget decision lives in `application/`; this layer
reads and writes rows. `version` is never hand-incremented — `trg_skills_bump_version`
is the single mechanism (DB-5).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.skills.domain.models import (
    Skill,
    SkillBinding,
    SkillFile,
    SkillFileKind,
    SkillScanStatus,
    SkillScope,
    SkillScopeCounts,
    SkillSource,
)
from contexts.skills.infrastructure import tables as t
from shared_kernel.auth.clients import now


def _row_to_skill(row: Any) -> Skill:
    return Skill(
        id=row.id,
        scope=SkillScope(row.scope),
        agent_id=row.agent_id,
        project_id=row.project_id,
        org_id=row.org_id,
        name=row.name,
        description=row.description,
        body=row.body,
        body_sha256=row.body_sha256,
        source=SkillSource(row.source),
        bundle_sha256=row.bundle_sha256,
        requires=tuple(row.requires or ()),
        allowed_tools=tuple(row.allowed_tools or ()),
        extra_frontmatter=dict(row.extra_frontmatter or {}),
        created_by=row.created_by,
        version=row.version,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


def _row_to_file(row: Any) -> SkillFile:
    return SkillFile(
        id=row.id,
        skill_id=row.skill_id,
        path=row.path,
        kind=SkillFileKind(row.kind),
        mime=row.mime,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        minio_key=row.minio_key,
        scan_status=SkillScanStatus(row.scan_status),
        extracted_chars=row.extracted_chars,
        created_at=row.created_at,
    )


def _row_to_binding(row: Any) -> SkillBinding:
    return SkillBinding(
        agent_id=row.agent_id,
        skill_id=row.skill_id,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
        cascade_deleted_at=row.cascade_deleted_at,
    )


class SkillRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, skill_id: uuid.UUID, *, include_deleted: bool = False) -> Skill | None:
        q = sa.select(t.skills).where(t.skills.c.id == skill_id)
        if not include_deleted:
            q = q.where(t.skills.c.deleted_at.is_(None))
        row = (await self._db.execute(q)).mappings().first()
        return _row_to_skill(row) if row else None

    async def get_by_name(self, *, scope: SkillScope, owner_id: uuid.UUID | None, name: str) -> Skill | None:
        """Live skill by (scope holder, name) — the [R31.03] uniqueness key."""
        q = sa.select(t.skills).where(
            t.skills.c.scope == scope.value,
            t.skills.c.name == name,
            t.skills.c.deleted_at.is_(None),
        )
        q = q.where(_owner_predicate(scope, owner_id))
        row = (await self._db.execute(q)).mappings().first()
        return _row_to_skill(row) if row else None

    async def list_for_scope(
        self,
        *,
        scope: SkillScope,
        owner_id: uuid.UUID | None,
        limit: int,
        offset: int,
        include_deleted: bool = False,
    ) -> tuple[list[Skill], int]:
        base = sa.select(t.skills).where(t.skills.c.scope == scope.value)
        base = base.where(_owner_predicate(scope, owner_id))
        if not include_deleted:
            base = base.where(t.skills.c.deleted_at.is_(None))

        total = (await self._db.execute(sa.select(sa.func.count()).select_from(base.subquery()))).scalar_one()
        rows = (
            (await self._db.execute(base.order_by(t.skills.c.name).limit(limit).offset(offset)))
            .mappings()
            .all()
        )
        return [_row_to_skill(r) for r in rows], int(total)

    async def create(
        self,
        *,
        scope: SkillScope,
        agent_id: uuid.UUID | None,
        project_id: uuid.UUID | None,
        org_id: uuid.UUID | None,
        name: str,
        description: str,
        body: str,
        body_sha256: str,
        source: SkillSource,
        bundle_sha256: str | None,
        requires: tuple[str, ...],
        allowed_tools: tuple[str, ...],
        extra_frontmatter: dict[str, Any],
        created_by: uuid.UUID | None,
    ) -> Skill:
        row = (
            (
                await self._db.execute(
                    t.skills.insert()
                    .values(
                        scope=scope.value,
                        agent_id=agent_id,
                        project_id=project_id,
                        org_id=org_id,
                        name=name,
                        description=description,
                        body=body,
                        body_sha256=body_sha256,
                        source=source.value,
                        bundle_sha256=bundle_sha256,
                        requires=list(requires),
                        allowed_tools=list(allowed_tools),
                        extra_frontmatter=extra_frontmatter,
                        created_by=created_by,
                    )
                    .returning(t.skills)
                )
            )
            .mappings()
            .one()
        )
        return _row_to_skill(row)

    async def update(self, skill_id: uuid.UUID, values: dict[str, Any]) -> Skill | None:
        """Patch `values`. Never pass `version` — the trigger owns it."""
        row = (
            (
                await self._db.execute(
                    t.skills.update()
                    .where(t.skills.c.id == skill_id, t.skills.c.deleted_at.is_(None))
                    .values(**values)
                    .returning(t.skills)
                )
            )
            .mappings()
            .first()
        )
        return _row_to_skill(row) if row else None

    async def soft_delete(self, skill_id: uuid.UUID) -> bool:
        res = await self._db.execute(
            t.skills.update()
            .where(t.skills.c.id == skill_id, t.skills.c.deleted_at.is_(None))
            .values(deleted_at=now())
        )
        return bool(res.rowcount)

    async def restore(self, skill_id: uuid.UUID) -> Skill | None:
        row = (
            (
                await self._db.execute(
                    t.skills.update()
                    .where(t.skills.c.id == skill_id, t.skills.c.deleted_at.is_not(None))
                    .values(deleted_at=None)
                    .returning(t.skills)
                )
            )
            .mappings()
            .first()
        )
        return _row_to_skill(row) if row else None

    async def cascade_owner_deleted(self, *, scope: SkillScope, owner_id: uuid.UUID) -> list[uuid.UUID]:
        """Soft-delete every live skill owned by a just-soft-deleted owner.

        Called inside the owner's own soft-delete transaction (the F-18 shape). The FK
        CASCADE cannot do this: it fires only on a physical DELETE, never on the
        soft-delete UPDATE — the trap 0054 exists to repair.
        """
        rows = (
            (
                await self._db.execute(
                    t.skills.update()
                    .where(
                        t.skills.c.scope == scope.value,
                        _owner_predicate(scope, owner_id),
                        t.skills.c.deleted_at.is_(None),
                    )
                    .values(deleted_at=now())
                    .returning(t.skills.c.id)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def count_by_scope(self) -> SkillScopeCounts:
        """[R31.11] — live skills per scope, for the admin metric."""
        rows = (
            await self._db.execute(
                sa.select(t.skills.c.scope, sa.func.count())
                .where(t.skills.c.deleted_at.is_(None))
                .group_by(t.skills.c.scope)
            )
        ).all()
        return SkillScopeCounts(counts={SkillScope(s): int(n) for s, n in rows})


class SkillBindingRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_live_for_agent(self, agent_id: uuid.UUID) -> list[Skill]:
        """The per-turn snapshot: live bindings joined to live skills, name-ordered.

        Deterministic ordering matters — the index block is rendered from this, and an
        unordered join would make the system prompt (and its token estimate) vary
        between turns for an unchanged agent.

        `id` is the tiebreak, and it is not decoration: `name` alone is only a total order
        while bound-set names are unique, which is a check-then-act rule no constraint
        enforces (it spans two tables). Ties therefore exist, and without the tiebreak the
        rows would come back in PostgreSQL's arbitrary order — so the system prompt could
        vary turn to turn after all, in exactly the case where the variation matters.
        """
        q = (
            sa.select(t.skills)
            .select_from(t.agent_skills.join(t.skills, t.agent_skills.c.skill_id == t.skills.c.id))
            .where(
                t.agent_skills.c.agent_id == agent_id,
                t.agent_skills.c.deleted_at.is_(None),
                t.agent_skills.c.cascade_deleted_at.is_(None),
                t.skills.c.deleted_at.is_(None),
            )
            .order_by(t.skills.c.name, t.skills.c.id)
        )
        rows = (await self._db.execute(q)).mappings().all()
        return [_row_to_skill(r) for r in rows]

    async def get(self, *, agent_id: uuid.UUID, skill_id: uuid.UUID) -> SkillBinding | None:
        row = (
            (
                await self._db.execute(
                    sa.select(t.agent_skills).where(
                        t.agent_skills.c.agent_id == agent_id,
                        t.agent_skills.c.skill_id == skill_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _row_to_binding(row) if row else None

    async def bind(self, *, agent_id: uuid.UUID, skill_id: uuid.UUID) -> SkillBinding:
        """Idempotent UPSERT clearing both delete timestamps (AC-36).

        A plain INSERT would collide with the live PK of a soft-unbound row, which makes
        re-binding — the most ordinary action in the feature — fail.
        """
        stmt = (
            pg_insert(t.agent_skills)
            .values(agent_id=agent_id, skill_id=skill_id)
            .on_conflict_do_update(
                constraint="pk_agent_skills",
                set_={"deleted_at": None, "cascade_deleted_at": None, "created_at": now()},
            )
            .returning(t.agent_skills)
        )
        row = (await self._db.execute(stmt)).mappings().one()
        return _row_to_binding(row)

    async def unbind(self, *, agent_id: uuid.UUID, skill_id: uuid.UUID) -> bool:
        """Explicit user unbind — sets `deleted_at`, which restore never clears.

        Matches only a **live** binding, so `cascade_deleted_at IS NULL` belongs here
        beside `deleted_at IS NULL`: a row a skill-delete already cascaded is not bound to
        anything, and stamping `deleted_at` on it would silently promote a reversible
        cascade into the one state restore refuses to undo (AC-37) — the binding would
        never come back.
        """
        res = await self._db.execute(
            t.agent_skills.update()
            .where(
                t.agent_skills.c.agent_id == agent_id,
                t.agent_skills.c.skill_id == skill_id,
                t.agent_skills.c.deleted_at.is_(None),
                t.agent_skills.c.cascade_deleted_at.is_(None),
            )
            .values(deleted_at=now())
        )
        return bool(res.rowcount)

    async def unbind_all_for_skill(self, skill_id: uuid.UUID) -> list[uuid.UUID]:
        """Cascade unbind — sets `cascade_deleted_at`, which restore *does* clear.

        Runs inside the skill's soft-delete transaction so unbind and deleted_at commit
        together (Q-27).
        """
        rows = (
            (
                await self._db.execute(
                    t.agent_skills.update()
                    .where(
                        t.agent_skills.c.skill_id == skill_id,
                        t.agent_skills.c.cascade_deleted_at.is_(None),
                        t.agent_skills.c.deleted_at.is_(None),
                    )
                    .values(cascade_deleted_at=now())
                    .returning(t.agent_skills.c.agent_id)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def unbind_all_for_agent(self, agent_id: uuid.UUID) -> list[uuid.UUID]:
        """Cascade unbind every skill bound to a just-soft-deleted agent (AC-38)."""
        rows = (
            (
                await self._db.execute(
                    t.agent_skills.update()
                    .where(
                        t.agent_skills.c.agent_id == agent_id,
                        t.agent_skills.c.cascade_deleted_at.is_(None),
                        t.agent_skills.c.deleted_at.is_(None),
                    )
                    .values(cascade_deleted_at=now())
                    .returning(t.agent_skills.c.skill_id)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def restore_cascaded_for_skill(self, skill_id: uuid.UUID) -> list[uuid.UUID]:
        """Restore only what *this* skill's delete cascaded (AC-37).

        Scoped to `cascade_deleted_at IS NOT NULL`, so a binding the user removed on
        purpose beforehand stays removed rather than silently returning with an
        executable body attached.
        """
        rows = (
            (
                await self._db.execute(
                    t.agent_skills.update()
                    .where(
                        t.agent_skills.c.skill_id == skill_id,
                        t.agent_skills.c.cascade_deleted_at.is_not(None),
                    )
                    .values(cascade_deleted_at=None)
                    .returning(t.agent_skills.c.agent_id)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def list_agent_ids_cascade_unbound_from(self, skill_id: uuid.UUID) -> list[uuid.UUID]:
        """Agents whose binding this skill's delete cascaded — i.e. what restore would
        re-attach. Excludes rows the user unbound explicitly, which restore leaves alone.
        """
        rows = (
            (
                await self._db.execute(
                    sa.select(t.agent_skills.c.agent_id).where(
                        t.agent_skills.c.skill_id == skill_id,
                        t.agent_skills.c.cascade_deleted_at.is_not(None),
                        t.agent_skills.c.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def list_agent_ids_bound_to(self, skill_id: uuid.UUID) -> list[uuid.UUID]:
        rows = (
            (
                await self._db.execute(
                    sa.select(t.agent_skills.c.agent_id).where(
                        t.agent_skills.c.skill_id == skill_id,
                        t.agent_skills.c.deleted_at.is_(None),
                        t.agent_skills.c.cascade_deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


class SkillFileRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_for_skill(self, skill_id: uuid.UUID) -> list[SkillFile]:
        rows = (
            (
                await self._db.execute(
                    sa.select(t.skill_files)
                    .where(t.skill_files.c.skill_id == skill_id)
                    .order_by(t.skill_files.c.path)
                )
            )
            .mappings()
            .all()
        )
        return [_row_to_file(r) for r in rows]

    async def get(self, file_id: uuid.UUID) -> SkillFile | None:
        row = (
            (await self._db.execute(sa.select(t.skill_files).where(t.skill_files.c.id == file_id)))
            .mappings()
            .first()
        )
        return None if row is None else _row_to_file(row)

    async def list_for_skills(self, skill_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[SkillFile]]:
        """Every listed skill's files, in one query.

        Batched rather than per-skill because the caller is `resolve_bound_set`, which
        runs on **every turn**: one query per bound skill would be an N+1 on the hottest
        path in the product. Rows carry metadata only — the bytes stay in MinIO and are
        fetched only when `read_skill` is actually asked for a file.
        """
        if not skill_ids:
            return {}
        rows = (
            (
                await self._db.execute(
                    sa.select(t.skill_files)
                    .where(t.skill_files.c.skill_id.in_(list(skill_ids)))
                    .order_by(t.skill_files.c.path)
                )
            )
            .mappings()
            .all()
        )
        out: dict[uuid.UUID, list[SkillFile]] = {sid: [] for sid in skill_ids}
        for r in rows:
            out[r["skill_id"]].append(_row_to_file(r))
        return out

    async def skill_ids_with_scripts(self, skill_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        """Which of these skills own at least one `scripts/` file (AC-20).

        Ids only, and deliberately not `list_for_skills`: the caller is the turn-time tap,
        which asks this about **every** live binding — including the ones it is about to
        drop — whereas the manifest is fetched only for the survivors. Answering an
        existence question with a full row set would fetch metadata for skills that never
        reach the snapshot, and widen the cheapest query on the hottest path.
        """
        if not skill_ids:
            return set()
        rows = await self._db.execute(
            sa.select(t.skill_files.c.skill_id)
            .where(
                t.skill_files.c.skill_id.in_(list(skill_ids)),
                t.skill_files.c.kind == SkillFileKind.SCRIPT.value,
            )
            .distinct()
        )
        return set(rows.scalars().all())

    async def list_paths_for_skill(self, skill_id: uuid.UUID) -> list[str]:
        """Just the paths — the collision check has no use for the rest of the row."""
        rows = (
            await self._db.execute(
                sa.select(t.skill_files.c.path).where(t.skill_files.c.skill_id == skill_id)
            )
        ).all()
        return [r[0] for r in rows]

    async def create(
        self,
        *,
        skill_id: uuid.UUID,
        path: str,
        kind: SkillFileKind,
        mime: str,
        size_bytes: int,
        sha256: str,
        minio_key: str,
        scan_status: SkillScanStatus,
        extracted_chars: int,
    ) -> SkillFile:
        row = (
            (
                await self._db.execute(
                    sa.insert(t.skill_files)
                    .values(
                        skill_id=skill_id,
                        path=path,
                        kind=kind.value,
                        mime=mime,
                        size_bytes=size_bytes,
                        sha256=sha256,
                        minio_key=minio_key,
                        scan_status=scan_status.value,
                        extracted_chars=extracted_chars,
                    )
                    .returning(t.skill_files)
                )
            )
            .mappings()
            .one()
        )
        return _row_to_file(row)

    async def update_content(
        self,
        file_id: uuid.UUID,
        *,
        size_bytes: int,
        sha256: str,
        minio_key: str,
        scan_status: SkillScanStatus,
        extracted_chars: int,
    ) -> SkillFile | None:
        """Re-point a file at new bytes.

        `scan_status` is a parameter rather than being preserved: new bytes have not been
        scanned, so carrying the old row's `clean` forward would let an edit smuggle past
        the gate AC-34 exists to hold. `path` and `kind` are absent — changing either is
        a different file, and `SKILL.md`'s references would still name the old one.
        """
        row = (
            (
                await self._db.execute(
                    sa.update(t.skill_files)
                    .where(t.skill_files.c.id == file_id)
                    .values(
                        size_bytes=size_bytes,
                        sha256=sha256,
                        minio_key=minio_key,
                        scan_status=scan_status.value,
                        extracted_chars=extracted_chars,
                    )
                    .returning(t.skill_files)
                )
            )
            .mappings()
            .first()
        )
        return None if row is None else _row_to_file(row)

    async def mark_scan(
        self, file_id: uuid.UUID, *, scan_status: SkillScanStatus, expected_sha256: str
    ) -> bool:
        """Record the scanner's verdict **on the bytes it was reached about**.

        The `sha256` predicate is the whole point, and this is where the RAG parallel
        breaks. `RagDocumentRepository.mark_scan` keys on the id alone, which is safe
        there because a document's bytes are immutable once written — `minio_path` is
        only ever assigned at create. `skill_files` has `update_content`, so the row's
        bytes can be replaced while a scan of the old ones is still in flight.

        Without this clause: upload 32 MiB of benign markdown, let its scan start, then
        edit the file to a small malicious payload. The edit's own scan finishes first
        and writes `quarantined`; the slow scan of the *old* bytes then completes and
        writes `clean` over it. Nothing rescans, so the gate AC-34 rests on is defeated
        permanently, and the attacker picks the ordering with two file sizes.

        Returns False when the row moved on — the verdict is about bytes that are no
        longer stored, and neither direction may land: a stale `quarantined` would brick
        a skill whose current file is fine.
        """
        result = await self._db.execute(
            sa.update(t.skill_files)
            .where(
                t.skill_files.c.id == file_id,
                t.skill_files.c.sha256 == expected_sha256,
            )
            .values(scan_status=scan_status.value)
        )
        return bool(result.rowcount)

    async def delete(self, file_id: uuid.UUID) -> bool:
        """A hard delete — `skill_files` has no `deleted_at`.

        Deliberate, and the asymmetry with `skills` is the point: Q-24 soft-deletes a
        skill because it is bound by many agents and the binding graph is the expensive
        thing to lose. A file is owned by exactly one skill and reachable from nothing
        else, so there is no graph to preserve. Deleting the *skill* leaves these rows
        intact for the 60-day window; it is only an explicit per-file delete that removes
        one, which is the user asking for exactly that.
        """
        result = await self._db.execute(sa.delete(t.skill_files).where(t.skill_files.c.id == file_id))
        return bool(result.rowcount)


def _owner_predicate(scope: SkillScope, owner_id: uuid.UUID | None) -> sa.ColumnElement[bool]:
    """Match the one owner column this scope populates.

    Platform skills own nothing, so the predicate is the all-NULL shape rather than a
    comparison against a caller-supplied id — an `owner_id` passed at platform scope is
    meaningless and must not silently filter.
    """
    if scope is SkillScope.AGENT:
        return t.skills.c.agent_id == owner_id
    if scope is SkillScope.PROJECT:
        return t.skills.c.project_id == owner_id
    if scope is SkillScope.ORG:
        return t.skills.c.org_id == owner_id
    return sa.and_(
        t.skills.c.agent_id.is_(None),
        t.skills.c.project_id.is_(None),
        t.skills.c.org_id.is_(None),
    )


__all__ = ["SkillBindingRepository", "SkillFileRepository", "SkillRepository"]
