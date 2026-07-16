"""Durable, race-free per-``(project, kind)`` embedding pin repository (F-11).

Shared by the file-RAG, Knowledge Map, and Concept Map create/delete paths. The
pin is the authoritative record of a project's fixed collection dimension; it is
independent of any config's lifecycle, so it survives config deletion (which the
derive-from-live-configs scheme did not).

``ensure`` serializes the read-then-insert window with a *transaction-scoped*
Postgres advisory lock keyed on ``(project_id, kind)`` — the
``(project_id, name)`` unique constraint the config tables carry does not cover
the dimension race, so two concurrent first-creates at different dimensions would
otherwise both commit. The lock is held for the caller's transaction, so
concurrent first-creates and a drop-empty teardown serialize on the same key.

Postgres-only (advisory lock + a single table read/insert, no Qdrant call), so
config CRUD never depends on Qdrant availability — preserving the property the
graph subsystems' pin lookups already had. One caller qualifies this: a create at a
*different* dimension than a configless retained pin runs the F-3 teardown retry under
this lock, and that does call Qdrant (see ``_retry_pending_teardown`` on each config
service). Nothing else does — a create matching the pin, or against no pin, stays
Postgres-only.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.embedding_pin import PinKind
from contexts.knowledge.infrastructure.embedding_pin_tables import project_embedding_pins as t


class EmbeddingPinRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def acquire_lock(self, project_id: uuid.UUID, kind: PinKind) -> None:
        """Take the transaction-scoped advisory lock for ``(project_id, kind)``.

        Held until the current transaction commits/rolls back. Re-entrant within
        a transaction (Postgres stacks the same key), so a caller that has already
        locked may call again cheaply. The drop-empty teardown holds this across
        its live-config check + :meth:`clear` so a concurrent create cannot slip a
        config in between the check and the pin clear.
        """
        await self._db.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"{project_id}:{kind.value}"},
        )

    async def try_acquire_lock(self, project_id: uuid.UUID, kind: PinKind) -> bool:
        """Take the ``(project_id, kind)`` lock only if it is free right now (F-3).

        The non-blocking twin of :meth:`acquire_lock`, for the one caller that has
        something better to do than wait: the create-path teardown retry. Losing this
        race means another transaction already holds the key and is doing that work,
        so a second retry would only repeat its Qdrant call. Returns ``True`` if the
        lock is now held for this transaction (released at commit/rollback like
        :meth:`acquire_lock`, and re-entrant with it), ``False`` if someone else has it.

        Never use this where correctness depends on the lock: a ``False`` here is
        "somebody else is on it", not "the invariant is satisfied".
        """
        return bool(
            (
                await self._db.execute(
                    sa.text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"{project_id}:{kind.value}"},
                )
            ).scalar_one()
        )

    async def list_all(self) -> list[sa.Row[Any]]:
        """Every pin row, for the F-3 teardown-retry sweep.

        Unfiltered because "is a teardown owed" is not answerable here: it depends on
        each kind's live configs, which live in three different tables the sweep's
        caller already holds repositories for. Pin rows are one per ``(project,
        kind)``, so this is bounded by projects x 3, not by data volume.
        """
        return list((await self._db.execute(t.select())).all())

    async def get(self, project_id: uuid.UUID, kind: PinKind) -> sa.Row[Any] | None:
        return (
            await self._db.execute(
                t.select().where(sa.and_(t.c.project_id == project_id, t.c.kind == kind.value))
            )
        ).first()

    async def ensure(
        self,
        *,
        project_id: uuid.UUID,
        kind: PinKind,
        provider: str,
        model: str,
        dim: int,
        on_conflict: Callable[[int, int], Exception],
    ) -> None:
        """Assert the project's pin for ``kind`` is ``dim``, creating it if absent.

        Under the advisory lock: if no pin exists, insert ``(provider, model,
        dim)``; if one exists at the same ``dim``, no-op; if it exists at a
        different ``dim``, raise ``on_conflict(existing_dim, dim)`` (the
        subsystem's typed dimension-conflict error). The lock serializes the
        read-then-insert so concurrent first-creates at different dimensions
        cannot both commit.
        """
        await self.acquire_lock(project_id, kind)
        existing = await self.get(project_id, kind)
        if existing is None:
            try:
                # SAVEPOINT: a unique-violation on a racing insert (e.g. a hashtext
                # collision routed the peer to a different lock key) rolls back only
                # this nested block, leaving the outer transaction usable for the
                # re-read below. Without it, asyncpg aborts the whole transaction on
                # IntegrityError and the follow-up SELECT would raise
                # PendingRollbackError instead of yielding the typed conflict.
                async with self._db.begin_nested():
                    await self._db.execute(
                        t.insert().values(
                            project_id=project_id,
                            kind=kind.value,
                            provider=provider,
                            model=model,
                            dim=dim,
                        )
                    )
                return
            except IntegrityError:
                existing = await self.get(project_id, kind)
        if existing is not None and int(existing.dim) != dim:
            raise on_conflict(int(existing.dim), dim)

    async def upsert(
        self,
        *,
        project_id: uuid.UUID,
        kind: PinKind,
        provider: str,
        model: str,
        dim: int,
    ) -> None:
        """Overwrite (or create) the pin for ``(project, kind)`` (F-13).

        Used only on an *allowed* embedding-model change (a never-built config):
        the update-path dimension guard has already established that no live
        sibling pins a different dimension, so an unconditional overwrite is safe
        and keeps the durable pin consistent with the config's newly persisted
        embedding. Unlike :meth:`ensure` it does not raise on a dimension
        change — the caller has decided the change is legitimate.
        """
        await self.acquire_lock(project_id, kind)
        existing = await self.get(project_id, kind)
        if existing is None:
            await self._db.execute(
                t.insert().values(
                    project_id=project_id,
                    kind=kind.value,
                    provider=provider,
                    model=model,
                    dim=dim,
                )
            )
        else:
            await self._db.execute(
                t.update()
                .where(sa.and_(t.c.project_id == project_id, t.c.kind == kind.value))
                .values(provider=provider, model=model, dim=dim)
            )

    async def clear(self, *, project_id: uuid.UUID, kind: PinKind) -> None:
        """Delete the pin row for ``(project_id, kind)``.

        Does NOT take the lock itself — the drop-empty teardown already holds it
        across the live-config check and this clear. A caller outside that flow
        should acquire the lock first.
        """
        await self._db.execute(
            t.delete().where(sa.and_(t.c.project_id == project_id, t.c.kind == kind.value))
        )


__all__ = ["EmbeddingPinRepository"]
