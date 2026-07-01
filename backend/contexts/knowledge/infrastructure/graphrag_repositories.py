"""Async repository for ``graphrag_configs`` (E.7).

All writes keep the caller's :class:`AsyncSession` transaction — the
service layer owns commit/rollback semantics so audit rows and state
transitions stay atomic.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.errors import GraphRagConfigAlreadyExists
from contexts.knowledge.domain.graphrag import BuildState, GraphRagConfig
from contexts.knowledge.infrastructure import graphrag_tables as t
from shared_kernel.auth.clients import now


def _row_to_config(row: Any) -> GraphRagConfig:
    return GraphRagConfig(
        id=row.id,
        project_id=row.project_id,
        agent_id=row.agent_id,
        builder_key_group_id=row.builder_key_group_id,
        trigger_config=dict(row.trigger_config or {}),
        last_build_at=row.last_build_at,
        last_build_state=BuildState(row.last_build_state),
        last_build_error=row.last_build_error,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


class GraphRagConfigRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        builder_key_group_id: uuid.UUID,
        trigger_config: dict[str, Any],
    ) -> GraphRagConfig:
        try:
            row = (
                await self._db.execute(
                    t.graphrag_configs.insert()
                    .values(
                        project_id=project_id,
                        agent_id=agent_id,
                        builder_key_group_id=builder_key_group_id,
                        trigger_config=trigger_config,
                    )
                    .returning(t.graphrag_configs)
                )
            ).one()
        except IntegrityError as exc:
            # R11.05 — `agent_id` is UNIQUE; a second create for the same
            # agent is a domain 409, not a 500.
            raise GraphRagConfigAlreadyExists(str(agent_id)) from exc
        return _row_to_config(row)

    async def get(
        self,
        config_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> GraphRagConfig | None:
        pred: sa.ColumnElement[bool] = t.graphrag_configs.c.id == config_id
        if not include_deleted:
            pred = sa.and_(pred, t.graphrag_configs.c.deleted_at.is_(None))
        row = (await self._db.execute(t.graphrag_configs.select().where(pred))).first()
        return _row_to_config(row) if row else None

    async def list_for_project(
        self,
        project_id: uuid.UUID,
    ) -> Sequence[GraphRagConfig]:
        rows = (
            await self._db.execute(
                t.graphrag_configs.select()
                .where(
                    sa.and_(
                        t.graphrag_configs.c.project_id == project_id,
                        t.graphrag_configs.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.graphrag_configs.c.created_at.desc())
            )
        ).all()
        return [_row_to_config(r) for r in rows]

    async def list_for_agents(
        self,
        agent_ids: Sequence[uuid.UUID],
    ) -> Sequence[GraphRagConfig]:
        ids = list(dict.fromkeys(agent_ids))
        if not ids:
            return []
        rows = (
            await self._db.execute(
                t.graphrag_configs.select()
                .where(
                    sa.and_(
                        t.graphrag_configs.c.agent_id.in_(ids),
                        t.graphrag_configs.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.graphrag_configs.c.created_at.desc())
            )
        ).all()
        return [_row_to_config(r) for r in rows]

    async def list_in_state(
        self,
        state: BuildState,
    ) -> Sequence[GraphRagConfig]:
        rows = (
            await self._db.execute(
                t.graphrag_configs.select().where(
                    sa.and_(
                        t.graphrag_configs.c.last_build_state == state.value,
                        t.graphrag_configs.c.deleted_at.is_(None),
                    )
                )
            )
        ).all()
        return [_row_to_config(r) for r in rows]

    async def set_state(
        self,
        *,
        config_id: uuid.UUID,
        state: BuildState,
        error: str | None = None,
        stamp_built_at: bool = False,
    ) -> None:
        values: dict[str, Any] = {
            "last_build_state": state.value,
            "last_build_error": error,
        }
        if stamp_built_at:
            values["last_build_at"] = now()
        await self._db.execute(
            t.graphrag_configs.update().where(t.graphrag_configs.c.id == config_id).values(**values)
        )

    async def soft_delete(self, config_id: uuid.UUID) -> None:
        await self._db.execute(
            t.graphrag_configs.update().where(t.graphrag_configs.c.id == config_id).values(deleted_at=now())
        )

    async def update(
        self,
        *,
        config_id: uuid.UUID,
        builder_key_group_id: uuid.UUID | None = None,
        trigger_config: dict[str, Any] | None = None,
    ) -> None:
        """Partial update of a GraphRAG config (R11.05 — edit trigger / key-group).

        Only the two fields the spec allows the user to mutate are accepted;
        ``agent_id`` is immutable post-create (1:1 with config in DB).
        """
        values: dict[str, Any] = {}
        if builder_key_group_id is not None:
            values["builder_key_group_id"] = builder_key_group_id
        if trigger_config is not None:
            values["trigger_config"] = trigger_config
        if not values:
            return
        await self._db.execute(
            t.graphrag_configs.update().where(t.graphrag_configs.c.id == config_id).values(**values)
        )


__all__ = ["GraphRagConfigRepository"]
