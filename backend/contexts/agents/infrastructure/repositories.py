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
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.domain.errors import (
    AgentNameTaken,
    AgentNotFound,
    AgentToolNotFound,
    AgentVersionMismatch,
    WorkspaceFileNotFound,
)
from contexts.agents.domain.models import (
    SINGLETON_TOOL_TYPES,
    Agent,
    AgentModelHint,
    AgentTool,
    AgentToolType,
    ContextMode,
    PromptStrategy,
    WorkspaceFile,
)
from contexts.agents.infrastructure import tables as t
from shared_kernel.auth.clients import now


def _row_to_agent(row: Any) -> Agent:
    return Agent(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        model_hint=AgentModelHint(row.model_hint),
        model_id=row.model_id,
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
        model_id: str | None,
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
                        model_id=model_id,
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
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[Agent]:
        q = (
            t.agents.select()
            .where(
                sa.and_(
                    t.agents.c.project_id == project_id,
                    t.agents.c.deleted_at.is_(None),
                )
            )
            .order_by(t.agents.c.created_at.desc())
            .offset(offset)
        )
        if limit is not None:
            q = q.limit(limit)
        rows = (await self._db.execute(q)).all()
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


def _row_to_tool(row: Any) -> AgentTool:
    return AgentTool(
        id=row.id,
        agent_id=row.agent_id,
        tool_type=AgentToolType(row.tool_type),
        enabled=bool(row.enabled),
        display_name=row.display_name,
        config=dict(row.config or {}),
        created_at=row.created_at,
    )


class AgentToolRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list(
        self,
        agent_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[AgentTool]:
        q = (
            t.agent_tools.select()
            .where(t.agent_tools.c.agent_id == agent_id)
            .order_by(t.agent_tools.c.created_at)
            .offset(offset)
        )
        if limit is not None:
            q = q.limit(limit)
        rows = (await self._db.execute(q)).all()
        return [_row_to_tool(r) for r in rows]

    async def list_for_agents(self, agent_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[AgentTool]]:
        if not agent_ids:
            return {}
        rows = (
            await self._db.execute(
                t.agent_tools.select()
                .where(t.agent_tools.c.agent_id.in_(list(agent_ids)))
                .order_by(t.agent_tools.c.agent_id, t.agent_tools.c.created_at)
            )
        ).all()
        result: dict[uuid.UUID, list[AgentTool]] = {aid: [] for aid in agent_ids}
        for row in rows:
            result[row.agent_id].append(_row_to_tool(row))
        return result

    async def get(self, *, agent_id: uuid.UUID, tool_id: uuid.UUID) -> AgentTool | None:
        row = (
            await self._db.execute(
                t.agent_tools.select().where(
                    sa.and_(
                        t.agent_tools.c.id == tool_id,
                        t.agent_tools.c.agent_id == agent_id,
                    )
                )
            )
        ).first()
        return _row_to_tool(row) if row else None

    async def get_singleton(self, *, agent_id: uuid.UUID, tool_type: AgentToolType) -> AgentTool | None:
        row = (
            await self._db.execute(
                t.agent_tools.select().where(
                    sa.and_(
                        t.agent_tools.c.agent_id == agent_id,
                        t.agent_tools.c.tool_type == tool_type.value,
                    )
                )
            )
        ).first()
        return _row_to_tool(row) if row else None

    async def add(
        self,
        *,
        agent_id: uuid.UUID,
        tool_type: AgentToolType,
        enabled: bool = True,
        display_name: str | None = None,
        config: dict[str, Any] | None = None,
        tool_id: uuid.UUID | None = None,
    ) -> AgentTool:
        values: dict[str, Any] = {
            "agent_id": agent_id,
            "tool_type": tool_type.value,
            "enabled": enabled,
            "display_name": display_name,
            "config": config or {},
        }
        if tool_id is not None:
            values["id"] = tool_id
        row = (
            await self._db.execute(
                t.agent_tools.insert().values(**values).returning(t.agent_tools)
            )
        ).one()
        return _row_to_tool(row)

    async def set_enabled(
        self,
        *,
        agent_id: uuid.UUID,
        tool_id: uuid.UUID,
        enabled: bool,
    ) -> AgentTool:
        row = (
            await self._db.execute(
                t.agent_tools.update()
                .where(
                    sa.and_(
                        t.agent_tools.c.id == tool_id,
                        t.agent_tools.c.agent_id == agent_id,
                    )
                )
                .values(enabled=enabled)
                .returning(t.agent_tools)
            )
        ).first()
        if row is None:
            raise AgentToolNotFound(str(tool_id))
        return _row_to_tool(row)

    async def patch(
        self,
        *,
        agent_id: uuid.UUID,
        tool_id: uuid.UUID,
        enabled: bool | None = None,
        display_name: str | None = None,
        config: dict[str, Any] | None = None,
        clear_display_name: bool = False,
    ) -> AgentTool:
        values: dict[str, Any] = {}
        if enabled is not None:
            values["enabled"] = enabled
        if clear_display_name:
            values["display_name"] = None
        elif display_name is not None:
            values["display_name"] = display_name
        if config is not None:
            values["config"] = config
        if not values:
            existing = await self.get(agent_id=agent_id, tool_id=tool_id)
            if existing is None:
                raise AgentToolNotFound(str(tool_id))
            return existing
        row = (
            await self._db.execute(
                t.agent_tools.update()
                .where(
                    sa.and_(
                        t.agent_tools.c.id == tool_id,
                        t.agent_tools.c.agent_id == agent_id,
                    )
                )
                .values(**values)
                .returning(t.agent_tools)
            )
        ).first()
        if row is None:
            raise AgentToolNotFound(str(tool_id))
        return _row_to_tool(row)

    async def remove(self, *, agent_id: uuid.UUID, tool_id: uuid.UUID) -> None:
        result = await self._db.execute(
            t.agent_tools.delete().where(
                sa.and_(
                    t.agent_tools.c.id == tool_id,
                    t.agent_tools.c.agent_id == agent_id,
                )
            )
        )
        if (result.rowcount or 0) == 0:
            raise AgentToolNotFound(str(tool_id))

    async def provision_singletons(
        self,
        *,
        agent_id: uuid.UUID,
        web_search: bool = True,
        code_interpreter: bool = False,
        file_workspace: bool = True,
        file_search_enabled: bool = False,
    ) -> None:
        """Idempotent: insert-or-ignore the four singleton rows for an agent."""
        rows = [
            (AgentToolType.HOSTED_WEB_SEARCH, web_search),
            (AgentToolType.HOSTED_CODE_INTERPRETER, code_interpreter),
            (AgentToolType.HOSTED_FILE_WORKSPACE, file_workspace),
            (AgentToolType.HOSTED_FILE_SEARCH, file_search_enabled),
        ]
        for tool_type, enabled in rows:
            stmt = (
                pg.insert(t.agent_tools)
                .values(
                    agent_id=agent_id,
                    tool_type=tool_type.value,
                    enabled=enabled,
                    config={},
                )
                .on_conflict_do_nothing(
                    index_elements=["agent_id", "tool_type"],
                    index_where=t.agent_tools.c.tool_type.in_(
                        [tt.value for tt in SINGLETON_TOOL_TYPES]
                    ),
                )
            )
            await self._db.execute(stmt)


def _row_to_workspace_file(row: Any) -> WorkspaceFile:
    return WorkspaceFile(
        id=row.id,
        agent_id=row.agent_id,
        path=row.path,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        mime=row.mime,
        minio_key=row.minio_key,
        created_by=row.created_by,
        created_at=row.created_at,
    )


class WorkspaceFileRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list(self, agent_id: uuid.UUID, *, limit: int = 500) -> Sequence[WorkspaceFile]:
        rows = (
            await self._db.execute(
                t.agent_workspace_files.select()
                .where(t.agent_workspace_files.c.agent_id == agent_id)
                .order_by(t.agent_workspace_files.c.path)
                .limit(limit)
            )
        ).all()
        return [_row_to_workspace_file(r) for r in rows]

    async def get(self, *, agent_id: uuid.UUID, file_id: uuid.UUID) -> WorkspaceFile | None:
        row = (
            await self._db.execute(
                t.agent_workspace_files.select().where(
                    sa.and_(
                        t.agent_workspace_files.c.id == file_id,
                        t.agent_workspace_files.c.agent_id == agent_id,
                    )
                )
            )
        ).first()
        return _row_to_workspace_file(row) if row else None

    async def count(self, agent_id: uuid.UUID) -> int:
        row = (
            await self._db.execute(
                sa.select(sa.func.count())
                .select_from(t.agent_workspace_files)
                .where(t.agent_workspace_files.c.agent_id == agent_id)
            )
        ).one()
        return int(row[0])

    async def get_by_path(self, *, agent_id: uuid.UUID, path: str) -> WorkspaceFile | None:
        row = (
            await self._db.execute(
                t.agent_workspace_files.select().where(
                    sa.and_(
                        t.agent_workspace_files.c.agent_id == agent_id,
                        t.agent_workspace_files.c.path == path,
                    )
                )
            )
        ).first()
        return _row_to_workspace_file(row) if row else None

    async def total_bytes(self, agent_id: uuid.UUID) -> int:
        row = (
            await self._db.execute(
                sa.select(sa.func.coalesce(sa.func.sum(t.agent_workspace_files.c.size_bytes), 0))
                .where(t.agent_workspace_files.c.agent_id == agent_id)
            )
        ).one()
        return int(row[0])

    async def upsert(
        self,
        *,
        agent_id: uuid.UUID,
        path: str,
        size_bytes: int,
        sha256: str,
        mime: str,
        minio_key: str,
        created_by: uuid.UUID | None,
    ) -> WorkspaceFile:
        stmt = (
            pg.insert(t.agent_workspace_files)
            .values(
                agent_id=agent_id,
                path=path,
                size_bytes=size_bytes,
                sha256=sha256,
                mime=mime,
                minio_key=minio_key,
                created_by=created_by,
            )
            .on_conflict_do_update(
                constraint="uq_agent_workspace_files_agent_path",
                set_={
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                    "mime": mime,
                    "minio_key": minio_key,
                    "created_by": created_by,
                    "created_at": sa.text("now()"),
                },
            )
            .returning(t.agent_workspace_files)
        )
        row = (await self._db.execute(stmt)).one()
        return _row_to_workspace_file(row)

    async def remove(self, *, agent_id: uuid.UUID, file_id: uuid.UUID) -> WorkspaceFile:
        existing = await self.get(agent_id=agent_id, file_id=file_id)
        if existing is None:
            raise WorkspaceFileNotFound(str(file_id))
        await self._db.execute(
            t.agent_workspace_files.delete().where(
                sa.and_(
                    t.agent_workspace_files.c.id == file_id,
                    t.agent_workspace_files.c.agent_id == agent_id,
                )
            )
        )
        return existing

    async def sha256_ref_count(self, *, agent_id: uuid.UUID, sha256: str) -> int:
        row = (
            await self._db.execute(
                sa.select(sa.func.count())
                .select_from(t.agent_workspace_files)
                .where(
                    sa.and_(
                        t.agent_workspace_files.c.agent_id == agent_id,
                        t.agent_workspace_files.c.sha256 == sha256,
                    )
                )
            )
        ).one()
        return int(row[0])


__all__ = ["AgentRepository", "AgentToolRepository", "WorkspaceFileRepository"]
