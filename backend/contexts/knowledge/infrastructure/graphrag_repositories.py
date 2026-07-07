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

from contexts.agent_groups.infrastructure import tables as ag
from contexts.knowledge.application.graphrag_ports import GraphRagConfigRepositoryPort
from contexts.knowledge.domain.errors import GraphRagConfigAlreadyExists
from contexts.knowledge.domain.graphrag import BuildState, GraphRagConfig
from contexts.knowledge.infrastructure import graphrag_tables as t
from shared_kernel.auth.clients import now


def _member_agent_id() -> Any:
    """Scalar subquery deriving the owning agent from the singleton group member.

    Phase 1 dropped ``graphrag_configs.agent_id`` (0044); the owning agent is
    now derived from the owner ``agent_group``'s membership. For a Phase-1
    singleton group there is exactly one member, so this is deterministic and
    equals the former column. ``LIMIT 1`` keeps it single-valued once multi-
    member groups arrive (Phase 2b); a non-``agent_group`` owner yields NULL.
    """
    m = ag.agent_group_members
    return (
        sa.select(m.c.agent_id)
        .where(m.c.agent_group_id == t.graphrag_configs.c.owner_agent_group_id)
        .limit(1)
        .scalar_subquery()
        .label("agent_id")
    )


def _config_select() -> Any:
    """SELECT over ``graphrag_configs`` with the derived ``agent_id`` column."""
    return sa.select(t.graphrag_configs, _member_agent_id())


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


class GraphRagConfigRepository(GraphRagConfigRepositoryPort):
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        owner_agent_group_id: uuid.UUID,
        builder_key_group_id: uuid.UUID,
        trigger_config: dict[str, Any],
    ) -> GraphRagConfig:
        try:
            await self._db.execute(
                t.graphrag_configs.insert().values(
                    project_id=project_id,
                    owner_agent_group_id=owner_agent_group_id,
                    owner_kind="agent_group",
                    builder_key_group_id=builder_key_group_id,
                    trigger_config=trigger_config,
                )
            )
        except IntegrityError as exc:
            # The owner partial-unique (uq_graphrag_configs_owner_agent_group)
            # enforces 1:1 ownership; a second create for the same owner is a
            # domain 409, not a 500.
            raise GraphRagConfigAlreadyExists(str(owner_agent_group_id)) from exc
        row = (
            await self._db.execute(
                _config_select().where(t.graphrag_configs.c.owner_agent_group_id == owner_agent_group_id)
            )
        ).one()
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
        row = (await self._db.execute(_config_select().where(pred))).first()
        return _row_to_config(row) if row else None

    async def list_for_project(
        self,
        project_id: uuid.UUID,
    ) -> Sequence[GraphRagConfig]:
        rows = (
            await self._db.execute(
                _config_select()
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
        """Configs owned by an agent_group any of ``agent_ids`` belongs to.

        Resolves ownership through the membership join, not the legacy
        ``agent_id`` column. For a Phase-1 singleton group (one member = the
        former owning agent) this returns exactly the config the pre-decouple
        ``WHERE agent_id IN (:ids)`` returned; the dedup keeps the result a set
        of distinct configs once multi-member groups arrive (Phase 2b).
        """
        ids = list(dict.fromkeys(agent_ids))
        if not ids:
            return []
        gc = t.graphrag_configs
        joined = gc.join(ag.agent_groups, ag.agent_groups.c.id == gc.c.owner_agent_group_id).join(
            ag.agent_group_members,
            ag.agent_group_members.c.agent_group_id == ag.agent_groups.c.id,
        )
        rows = (
            await self._db.execute(
                sa.select(gc, _member_agent_id())
                .select_from(joined)
                .where(
                    sa.and_(
                        ag.agent_group_members.c.agent_id.in_(ids),
                        gc.c.deleted_at.is_(None),
                        ag.agent_groups.c.deleted_at.is_(None),
                    )
                )
                .order_by(gc.c.created_at.desc())
            )
        ).all()
        seen: set[uuid.UUID] = set()
        out: list[GraphRagConfig] = []
        for r in rows:
            if r.id in seen:
                continue
            seen.add(r.id)
            out.append(_row_to_config(r))
        return out

    async def list_in_state(
        self,
        state: BuildState,
    ) -> Sequence[GraphRagConfig]:
        rows = (
            await self._db.execute(
                _config_select().where(
                    sa.and_(
                        t.graphrag_configs.c.last_build_state == state.value,
                        t.graphrag_configs.c.deleted_at.is_(None),
                    )
                )
            )
        ).all()
        return [_row_to_config(r) for r in rows]

    async def list_all_ids(self, *, include_deleted: bool = False) -> set[uuid.UUID]:
        stmt = sa.select(t.graphrag_configs.c.id)
        if not include_deleted:
            stmt = stmt.where(t.graphrag_configs.c.deleted_at.is_(None))
        rows = (await self._db.execute(stmt)).all()
        return {r.id for r in rows}

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
