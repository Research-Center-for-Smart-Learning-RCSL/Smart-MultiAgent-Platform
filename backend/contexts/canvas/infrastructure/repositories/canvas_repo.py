"""Canvas repository -- data access for canvas, objects, and snapshots."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.application.crdt_relay import _EXCALIDRAW_ELEMENT_KINDS
from contexts.canvas.domain.models import (
    Canvas,
    CanvasObject,
    CanvasObjectKind,
    CanvasSnapshot,
)
from contexts.canvas.infrastructure import tables as t
from shared_kernel.auth.clients import now

_EXCALIDRAW_TO_DOMAIN_KIND = {v: k for k, v in _EXCALIDRAW_ELEMENT_KINDS.items()}


def _row_to_canvas(row: Any) -> Canvas:
    return Canvas(
        id=row.id,
        chatroom_id=row.chatroom_id,
        expose_to_agents=bool(row.expose_to_agents),
        created_at=row.created_at,
        deleted_at=row.deleted_at,
        crdt_state=row.crdt_state,
    )


def _row_to_object(row: Any) -> CanvasObject:
    return CanvasObject(
        id=row.id,
        canvas_id=row.canvas_id,
        kind=CanvasObjectKind(row.kind),
        content=row.content,
        minio_path=row.minio_path,
        position_x=row.position_x,
        position_y=row.position_y,
        width=row.width,
        height=row.height,
        z_index=row.z_index,
        style=row.style or {},
        created_by_user_id=row.created_by_user_id,
        created_by_guest_id=row.created_by_guest_id,
        created_by_agent_id=row.created_by_agent_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_snapshot(row: Any) -> CanvasSnapshot:
    return CanvasSnapshot(
        id=row.id,
        canvas_id=row.canvas_id,
        snapshot_data=row.snapshot_data,
        agent_digest=row.agent_digest,
        created_by_user_id=row.created_by_user_id,
        label=row.label,
        created_at=row.created_at,
    )


class CanvasRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ---- canvas CRUD -------------------------------------------------------

    async def get_by_chatroom(self, chatroom_id: uuid.UUID) -> Canvas | None:
        row = (
            await self._db.execute(
                t.canvases.select().where(
                    sa.and_(
                        t.canvases.c.chatroom_id == chatroom_id,
                        t.canvases.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_canvas(row) if row else None

    async def get(self, canvas_id: uuid.UUID) -> Canvas | None:
        row = (
            await self._db.execute(
                t.canvases.select().where(
                    sa.and_(
                        t.canvases.c.id == canvas_id,
                        t.canvases.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_canvas(row) if row else None

    async def create(self, *, chatroom_id: uuid.UUID) -> Canvas:
        row = (
            await self._db.execute(t.canvases.insert().values(chatroom_id=chatroom_id).returning(t.canvases))
        ).one()
        return _row_to_canvas(row)

    async def upsert_for_chatroom(self, chatroom_id: uuid.UUID) -> Canvas:
        """Atomically get-or-create a canvas for *chatroom_id*.

        Handles the TOCTOU race (concurrent first-time opens) and
        reactivation of a soft-deleted row.  Active rows are left
        untouched; soft-deleted rows are reactivated with clean defaults.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(t.canvases)
            .values(chatroom_id=chatroom_id)
            .on_conflict_do_update(
                index_elements=[t.canvases.c.chatroom_id],
                set_={
                    "deleted_at": None,
                    "expose_to_agents": sa.case(
                        (t.canvases.c.deleted_at.isnot(None), sa.literal(True)),
                        else_=t.canvases.c.expose_to_agents,
                    ),
                    "crdt_state": sa.case(
                        (t.canvases.c.deleted_at.isnot(None), sa.null()),
                        else_=t.canvases.c.crdt_state,
                    ),
                },
            )
            .returning(t.canvases)
        )
        row = (await self._db.execute(stmt)).one()
        return _row_to_canvas(row)

    async def update_settings(self, canvas_id: uuid.UUID, *, expose_to_agents: bool) -> Canvas | None:
        row = (
            await self._db.execute(
                t.canvases.update()
                .where(
                    sa.and_(
                        t.canvases.c.id == canvas_id,
                        t.canvases.c.deleted_at.is_(None),
                    )
                )
                .values(expose_to_agents=expose_to_agents)
                .returning(t.canvases)
            )
        ).first()
        return _row_to_canvas(row) if row else None

    async def soft_delete(self, canvas_id: uuid.UUID) -> None:
        await self._db.execute(
            t.canvases.update().where(t.canvases.c.id == canvas_id).values(deleted_at=now())
        )

    # ---- canvas objects ----------------------------------------------------

    async def list_objects(
        self, canvas_id: uuid.UUID, *, limit: int = 500, offset: int = 0
    ) -> Sequence[CanvasObject]:
        rows = (
            await self._db.execute(
                t.canvas_objects.select()
                .where(t.canvas_objects.c.canvas_id == canvas_id)
                .order_by(t.canvas_objects.c.z_index, t.canvas_objects.c.created_at)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [_row_to_object(r) for r in rows]

    async def count_objects(self, canvas_id: uuid.UUID) -> int:
        result = await self._db.execute(
            sa.select(sa.func.count())
            .select_from(t.canvas_objects)
            .where(t.canvas_objects.c.canvas_id == canvas_id)
        )
        return result.scalar_one()

    async def count_images(self, canvas_id: uuid.UUID) -> int:
        result = await self._db.execute(
            sa.select(sa.func.count())
            .select_from(t.canvas_objects)
            .where(
                sa.and_(
                    t.canvas_objects.c.canvas_id == canvas_id,
                    t.canvas_objects.c.kind == CanvasObjectKind.IMAGE.value,
                )
            )
        )
        return result.scalar_one()

    async def get_object(self, object_id: uuid.UUID) -> CanvasObject | None:
        row = (
            await self._db.execute(t.canvas_objects.select().where(t.canvas_objects.c.id == object_id))
        ).first()
        return _row_to_object(row) if row else None

    async def create_object(self, *, values: dict[str, Any]) -> CanvasObject:
        row = (
            await self._db.execute(t.canvas_objects.insert().values(**values).returning(t.canvas_objects))
        ).one()
        return _row_to_object(row)

    async def update_object(
        self, object_id: uuid.UUID, *, canvas_id: uuid.UUID, values: dict[str, Any]
    ) -> CanvasObject | None:
        values["updated_at"] = now()
        row = (
            await self._db.execute(
                t.canvas_objects.update()
                .where(
                    sa.and_(
                        t.canvas_objects.c.id == object_id,
                        t.canvas_objects.c.canvas_id == canvas_id,
                    )
                )
                .values(**values)
                .returning(t.canvas_objects)
            )
        ).first()
        return _row_to_object(row) if row else None

    async def delete_object(self, object_id: uuid.UUID, *, canvas_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            t.canvas_objects.delete().where(
                sa.and_(
                    t.canvas_objects.c.id == object_id,
                    t.canvas_objects.c.canvas_id == canvas_id,
                )
            )
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def batch_delete_objects(self, object_ids: Sequence[uuid.UUID], *, canvas_id: uuid.UUID) -> int:
        if not object_ids:
            return 0
        result = await self._db.execute(
            t.canvas_objects.delete().where(
                sa.and_(
                    t.canvas_objects.c.id.in_(object_ids),
                    t.canvas_objects.c.canvas_id == canvas_id,
                )
            )
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def sync_text_from_crdt(
        self,
        canvas_id: uuid.UUID,
        elements: list[dict[str, Any]],
    ) -> None:
        """Synchronise text content from CRDT elements into canvas_objects for FTS.

        Upserts rows keyed by element id: updates ``content`` (which triggers
        the ``content_tsv`` trigger) for elements that carry text, and removes
        stale rows whose element was deleted in the CRDT doc.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        live_ids: set[uuid.UUID] = set()
        for elem in elements:
            raw_id = elem.get("id")
            if not raw_id:
                continue
            try:
                eid = uuid.UUID(raw_id)
            except ValueError:
                continue
            live_ids.add(eid)
            text = elem.get("text") or None
            kind_raw = elem.get("type", "rectangle")
            kind = _EXCALIDRAW_TO_DOMAIN_KIND.get(kind_raw, "shape")

            stmt = (
                pg_insert(t.canvas_objects)
                .values(
                    id=eid,
                    canvas_id=canvas_id,
                    kind=kind,
                    content=text,
                    position_x=float(elem.get("x", 0)),
                    position_y=float(elem.get("y", 0)),
                    width=float(elem.get("width", 0)),
                    height=float(elem.get("height", 0)),
                )
                .on_conflict_do_update(
                    index_elements=[t.canvas_objects.c.id],
                    set_={
                        "content": text,
                        "position_x": float(elem.get("x", 0)),
                        "position_y": float(elem.get("y", 0)),
                        "width": float(elem.get("width", 0)),
                        "height": float(elem.get("height", 0)),
                        "updated_at": now(),
                    },
                )
            )
            await self._db.execute(stmt)

        if live_ids:
            await self._db.execute(
                t.canvas_objects.delete().where(
                    sa.and_(
                        t.canvas_objects.c.canvas_id == canvas_id,
                        t.canvas_objects.c.id.notin_(live_ids),
                    )
                )
            )
        else:
            await self._db.execute(
                t.canvas_objects.delete().where(
                    t.canvas_objects.c.canvas_id == canvas_id,
                )
            )

    # ---- search -------------------------------------------------------------

    async def search(
        self,
        canvas_id: uuid.UUID,
        query: str,
        *,
        limit: int = 50,
    ) -> Sequence[tuple[CanvasObject, float, str]]:
        config = sa.cast(sa.literal("english"), REGCONFIG)
        tsq = sa.func.plainto_tsquery(config, sa.literal(query))
        stmt = (
            sa.select(
                t.canvas_objects,
                sa.func.ts_rank_cd(t.canvas_objects.c.content_tsv, tsq).label("rank"),
                sa.func.ts_headline(
                    config,
                    t.canvas_objects.c.content,
                    tsq,
                    sa.literal("StartSel=<mark>,StopSel=</mark>,MaxWords=35,MinWords=15,ShortWord=3"),
                ).label("snippet"),
            )
            .where(
                sa.and_(
                    t.canvas_objects.c.canvas_id == canvas_id,
                    t.canvas_objects.c.content_tsv.op("@@")(tsq),
                )
            )
            .order_by(
                sa.desc("rank"),
                t.canvas_objects.c.created_at.desc(),
                t.canvas_objects.c.id.desc(),
            )
            .limit(limit)
        )
        rows = (await self._db.execute(stmt)).all()
        return [(_row_to_object(r), r.rank, r.snippet) for r in rows]

    # ---- crdt state --------------------------------------------------------

    async def get_crdt_state(self, canvas_id: uuid.UUID) -> bytes | None:
        row = (
            await self._db.execute(
                sa.select(t.canvases.c.crdt_state).where(
                    sa.and_(
                        t.canvases.c.id == canvas_id,
                        t.canvases.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return row.crdt_state if row else None

    async def update_crdt_state(self, canvas_id: uuid.UUID, state: bytes) -> None:
        await self._db.execute(
            t.canvases.update().where(t.canvases.c.id == canvas_id).values(crdt_state=state)
        )

    # ---- snapshots ---------------------------------------------------------

    async def list_snapshots(
        self, canvas_id: uuid.UUID, *, limit: int = 20, offset: int = 0
    ) -> Sequence[CanvasSnapshot]:
        rows = (
            await self._db.execute(
                t.canvas_snapshots.select()
                .where(t.canvas_snapshots.c.canvas_id == canvas_id)
                .order_by(t.canvas_snapshots.c.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [_row_to_snapshot(r) for r in rows]

    async def latest_snapshot(self, canvas_id: uuid.UUID) -> CanvasSnapshot | None:
        row = (
            await self._db.execute(
                t.canvas_snapshots.select()
                .where(t.canvas_snapshots.c.canvas_id == canvas_id)
                .order_by(t.canvas_snapshots.c.created_at.desc())
                .limit(1)
            )
        ).first()
        return _row_to_snapshot(row) if row else None

    async def get_snapshot(self, snapshot_id: uuid.UUID, *, canvas_id: uuid.UUID) -> CanvasSnapshot | None:
        row = (
            await self._db.execute(
                t.canvas_snapshots.select().where(
                    sa.and_(
                        t.canvas_snapshots.c.id == snapshot_id,
                        t.canvas_snapshots.c.canvas_id == canvas_id,
                    )
                )
            )
        ).first()
        return _row_to_snapshot(row) if row else None

    async def count_snapshots(self, canvas_id: uuid.UUID) -> int:
        result = await self._db.execute(
            sa.select(sa.func.count())
            .select_from(t.canvas_snapshots)
            .where(t.canvas_snapshots.c.canvas_id == canvas_id)
        )
        return result.scalar_one()

    async def delete_oldest_snapshot(self, canvas_id: uuid.UUID) -> None:
        oldest = (
            await self._db.execute(
                sa.select(t.canvas_snapshots.c.id)
                .where(t.canvas_snapshots.c.canvas_id == canvas_id)
                .order_by(t.canvas_snapshots.c.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if oldest is not None:
            await self._db.execute(t.canvas_snapshots.delete().where(t.canvas_snapshots.c.id == oldest))

    async def create_snapshot(self, *, values: dict[str, Any]) -> CanvasSnapshot:
        row = (
            await self._db.execute(t.canvas_snapshots.insert().values(**values).returning(t.canvas_snapshots))
        ).one()
        return _row_to_snapshot(row)


__all__ = ["CanvasRepository"]
