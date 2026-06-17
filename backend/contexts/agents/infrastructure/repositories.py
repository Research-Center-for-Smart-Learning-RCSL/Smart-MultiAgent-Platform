"""Agents repositories — pure SQL; no cross-context joins.

Everything that needs data from tenancy / keys goes through a facade
call in the application layer — this module only talks to its own tables
and to FKs via row existence (not joins).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.domain.errors import (
    AgentNameTaken,
    AgentNotFound,
    AgentVersionMismatch,
    McpBindingNotFound,
)
from contexts.agents.domain.models import (
    Agent,
    AgentModelHint,
    ContextMode,
    McpBinding,
    McpSource,
    PromptStrategy,
)
from contexts.agents.infrastructure import tables as t
from shared_kernel.auth.clients import now


def _row_to_agent(row: Any) -> Agent:
    return Agent(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        model_hint=AgentModelHint(row.model_hint),
        key_group_id=row.key_group_id,
        system_prompt=row.system_prompt,
        prompt_strategy=PromptStrategy(row.prompt_strategy),
        rag_config_id=row.rag_config_id,
        graphrag_config_id=row.graphrag_config_id,
        context_mode=ContextMode(row.context_mode),
        context_token_cap=row.context_token_cap,
        a2a_enabled=row.a2a_enabled,
        wakeup_config=dict(row.wakeup_config or {}),
        wakeup_authored_snapshot=dict(row.wakeup_authored_snapshot) if row.wakeup_authored_snapshot else None,
        workflow_capabilities=dict(row.workflow_capabilities or {}),
        version=row.version,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
    )


def _row_to_binding(row: Any) -> McpBinding:
    return McpBinding(
        id=row.id,
        agent_id=row.agent_id,
        source=McpSource(row.source),
        reference=row.reference,
        allowed_tools=tuple(row.allowed_tools or ()),
        config=dict(row.config or {}),
        created_at=row.created_at,
    )


class AgentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def count_active(self, project_id: uuid.UUID) -> int:
        row = (
            await self._db.execute(
                sa.select(sa.func.count())
                .select_from(t.agents)
                .where(
                    sa.and_(
                        t.agents.c.project_id == project_id,
                        t.agents.c.deleted_at.is_(None),
                    )
                )
            )
        ).one()
        return int(row[0])

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        name: str,
        model_hint: AgentModelHint,
        key_group_id: uuid.UUID,
        system_prompt: str,
        prompt_strategy: PromptStrategy,
        rag_config_id: uuid.UUID | None,
        graphrag_config_id: uuid.UUID | None,
        context_mode: ContextMode,
        context_token_cap: int | None,
        a2a_enabled: bool,
        wakeup_config: dict[str, Any],
        wakeup_authored_snapshot: dict[str, Any] | None = None,
        workflow_capabilities: dict[str, Any],
    ) -> Agent:
        try:
            row = (
                await self._db.execute(
                    t.agents.insert()
                    .values(
                        project_id=project_id,
                        name=name,
                        model_hint=model_hint.value,
                        key_group_id=key_group_id,
                        system_prompt=system_prompt,
                        prompt_strategy=prompt_strategy.value,
                        rag_config_id=rag_config_id,
                        graphrag_config_id=graphrag_config_id,
                        context_mode=context_mode.value,
                        context_token_cap=context_token_cap,
                        a2a_enabled=a2a_enabled,
                        wakeup_config=wakeup_config,
                        wakeup_authored_snapshot=wakeup_authored_snapshot,
                        workflow_capabilities=workflow_capabilities,
                    )
                    .returning(t.agents)
                )
            ).one()
        except IntegrityError as exc:
            msg = str(exc.orig or exc).lower()
            if "uq_agents_project_name_active" in msg:
                raise AgentNameTaken(name) from exc
            raise
        return _row_to_agent(row)

    async def get(self, agent_id: uuid.UUID, *, include_deleted: bool = False) -> Agent | None:
        predicate: sa.ColumnElement[bool] = t.agents.c.id == agent_id
        if not include_deleted:
            predicate = sa.and_(predicate, t.agents.c.deleted_at.is_(None))
        row = (await self._db.execute(t.agents.select().where(predicate))).first()
        return _row_to_agent(row) if row else None

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Agent]:
        rows = (
            await self._db.execute(
                t.agents.select()
                .where(
                    sa.and_(
                        t.agents.c.project_id == project_id,
                        t.agents.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.agents.c.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [_row_to_agent(r) for r in rows]

    async def patch(
        self,
        *,
        agent_id: uuid.UUID,
        expected_version: int,
        values: dict[str, Any],
    ) -> Agent:
        """Apply a partial patch.

        `version` is bumped by the `smap_bump_version` BEFORE-UPDATE trigger
        (migration 0029) — repository code must never increment it by hand.
        """
        if not values:
            # Caller must send at least one field; empty patch is nonsense.
            existing = await self.get(agent_id)
            if existing is None:
                raise AgentNotFound(str(agent_id))
            if existing.version != expected_version:
                raise AgentVersionMismatch(str(agent_id))
            return existing
        stmt = (
            t.agents.update()
            .where(
                sa.and_(
                    t.agents.c.id == agent_id,
                    t.agents.c.version == expected_version,
                    t.agents.c.deleted_at.is_(None),
                )
            )
            .values(**values)
            .returning(t.agents)
        )
        try:
            row = (await self._db.execute(stmt)).first()
        except IntegrityError as exc:
            msg = str(exc.orig or exc).lower()
            if "uq_agents_project_name_active" in msg:
                raise AgentNameTaken(str(values.get("name"))) from exc
            raise
        if row is None:
            # Either wrong version or row missing; disambiguate for callers.
            probe = await self.get(agent_id)
            if probe is None:
                raise AgentNotFound(str(agent_id))
            raise AgentVersionMismatch(str(agent_id))
        return _row_to_agent(row)

    async def list_with_authored_snapshot(self) -> Sequence[Agent]:
        """Active agents that have a non-null wakeup_authored_snapshot (G.5)."""
        rows = (
            await self._db.execute(
                t.agents.select().where(
                    sa.and_(
                        t.agents.c.deleted_at.is_(None),
                        t.agents.c.wakeup_authored_snapshot.isnot(None),
                    )
                )
            )
        ).all()
        return [_row_to_agent(r) for r in rows]

    async def soft_delete(self, *, agent_id: uuid.UUID, expected_version: int) -> Agent:
        stmt = (
            t.agents.update()
            .where(
                sa.and_(
                    t.agents.c.id == agent_id,
                    t.agents.c.version == expected_version,
                    t.agents.c.deleted_at.is_(None),
                )
            )
            .values(deleted_at=now())  # version bumped by smap_bump_version trigger
            .returning(t.agents)
        )
        row = (await self._db.execute(stmt)).first()
        if row is None:
            probe = await self.get(agent_id, include_deleted=True)
            if probe is None or probe.deleted_at is not None:
                raise AgentNotFound(str(agent_id))
            raise AgentVersionMismatch(str(agent_id))
        return _row_to_agent(row)


class AgentMcpBindingRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list(self, agent_id: uuid.UUID) -> Sequence[McpBinding]:
        rows = (
            await self._db.execute(
                t.agent_mcp_servers.select()
                .where(t.agent_mcp_servers.c.agent_id == agent_id)
                .order_by(t.agent_mcp_servers.c.created_at)
            )
        ).all()
        return [_row_to_binding(r) for r in rows]

    async def add(
        self,
        *,
        agent_id: uuid.UUID,
        source: McpSource,
        reference: str,
        allowed_tools: Sequence[str],
        config: dict[str, Any],
    ) -> McpBinding:
        row = (
            await self._db.execute(
                t.agent_mcp_servers.insert()
                .values(
                    agent_id=agent_id,
                    source=source.value,
                    reference=reference,
                    allowed_tools=list(allowed_tools),
                    config=config,
                )
                .returning(t.agent_mcp_servers)
            )
        ).one()
        return _row_to_binding(row)

    async def patch(
        self,
        *,
        agent_id: uuid.UUID,
        binding_id: uuid.UUID,
        allowed_tools: Sequence[str] | None,
        config: dict[str, Any] | None,
    ) -> McpBinding:
        """Update allowed_tools and/or config on an existing binding."""
        values: dict[str, Any] = {}
        if allowed_tools is not None:
            values["allowed_tools"] = list(allowed_tools)
        if config is not None:
            values["config"] = config
        if not values:
            row = (
                await self._db.execute(
                    t.agent_mcp_servers.select().where(
                        sa.and_(
                            t.agent_mcp_servers.c.id == binding_id,
                            t.agent_mcp_servers.c.agent_id == agent_id,
                        )
                    )
                )
            ).first()
            if row is None:
                raise McpBindingNotFound(str(binding_id))
            return _row_to_binding(row)

        row = (
            await self._db.execute(
                t.agent_mcp_servers.update()
                .where(
                    sa.and_(
                        t.agent_mcp_servers.c.id == binding_id,
                        t.agent_mcp_servers.c.agent_id == agent_id,
                    )
                )
                .values(**values)
                .returning(t.agent_mcp_servers)
            )
        ).first()
        if row is None:
            raise McpBindingNotFound(str(binding_id))
        return _row_to_binding(row)

    async def remove(self, *, agent_id: uuid.UUID, binding_id: uuid.UUID) -> None:
        result = await self._db.execute(
            t.agent_mcp_servers.delete().where(
                sa.and_(
                    t.agent_mcp_servers.c.id == binding_id,
                    t.agent_mcp_servers.c.agent_id == agent_id,
                )
            )
        )
        if (result.rowcount or 0) == 0:
            raise McpBindingNotFound(str(binding_id))


__all__ = ["AgentMcpBindingRepository", "AgentRepository"]
