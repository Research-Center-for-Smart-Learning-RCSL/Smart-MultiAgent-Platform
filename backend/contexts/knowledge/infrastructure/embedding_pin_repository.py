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
graph subsystems' pin lookups already had.
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
                # A racing insert beat us despite the lock (e.g. a hashtext
                # collision routed the peer to a different lock key). Re-read and
                # fall through to the dimension comparison.
                existing = await self.get(project_id, kind)
        if existing is not None and int(existing.dim) != dim:
            raise on_conflict(int(existing.dim), dim)

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
