"""Agent use-cases (§22.6) — the single write path for the agents context.

Guardrails enforced here:

- **1 000 active agents / project cap** (R9.01).
- **Key Group must live in the same project** as the agent (R7.02 spirit:
  Key Groups are project-scoped; an agent pointing at another project's
  Group would silently break isolation).
- **Optimistic locking** on patch / delete via `If-Match: <version>`.
- **Audit tap** for every state-changing call.

SoC:
- The service owns the cross-table invariants (cap, key-group project check).
- Repositories own storage shape and FK-level errors.
- The router owns pydantic schemas, auth guards, and If-Match parsing.
"""

from __future__ import annotations

import struct
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.domain.errors import (
    AgentCapExceeded,
    AgentNotFound,
    GraphRagConfigOutOfProject,
    KeyGroupOutOfProject,
    RagConfigOutOfProject,
)
from contexts.agents.domain.models import (
    Agent,
    AgentDraft,
    ContextMode,
    McpBinding,
    McpSource,
    PromptStrategy,
)
from contexts.agents.infrastructure.repositories import (
    AgentMcpBindingRepository,
    AgentRepository,
)
from contexts.keys.interfaces.facade import KeysFacade
from contexts.knowledge.interfaces.facade import KnowledgeFacade
from shared_kernel import audit

_AGENT_CAP_PER_PROJECT = 1000
# Sentinel for system-initiated wakeup patches (§22.6). Never maps to a real
# user row — authored-snapshot overwrites are skipped when the actor is the system.
_SYSTEM_ACTOR_ID = uuid.UUID(int=0)


class AgentService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._agents = AgentRepository(db)
        self._bindings = AgentMcpBindingRepository(db)
        self._keys = KeysFacade(db)
        self._knowledge = KnowledgeFacade(db)

    async def _assert_key_group_in_project(self, *, key_group_id: uuid.UUID, project_id: uuid.UUID) -> None:
        group = await self._keys.get_key_group(key_group_id)
        if group is None or group.project_id != project_id:
            raise KeyGroupOutOfProject(f"key_group {key_group_id} is not in project {project_id}")

    async def _assert_rag_config_in_project(self, *, rag_config_id: uuid.UUID, project_id: uuid.UUID) -> None:
        """SEC-H1 — a RAG config attached to an agent must live in the same
        project, else the agent would pull another tenant's document chunks
        into context at retrieval time (the Qdrant collection is keyed on the
        *config's* project_id). Mirrors `_assert_key_group_in_project`.
        """
        cfg = await self._knowledge.get_rag_config(rag_config_id)
        if cfg is None or cfg.project_id != project_id:
            raise RagConfigOutOfProject(f"rag_config {rag_config_id} is not in project {project_id}")

    async def _assert_graphrag_config_in_project(
        self, *, graphrag_config_id: uuid.UUID, project_id: uuid.UUID
    ) -> None:
        """SEC-H1 — same cross-tenant guard for an attached GraphRAG config."""
        cfg = await self._knowledge.get_graphrag_config(graphrag_config_id)
        if cfg is None or cfg.project_id != project_id:
            raise GraphRagConfigOutOfProject(
                f"graphrag_config {graphrag_config_id} is not in project {project_id}"
            )

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        draft: AgentDraft,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Agent:
        # R9.01 cap — serialise concurrent creates for the same project so
        # the count check and INSERT are atomic. The advisory lock is released
        # automatically when this transaction commits or rolls back.
        lock_id = struct.unpack(">q", project_id.bytes[:8])[0]
        await self._db.execute(sa.text("SELECT pg_advisory_xact_lock(:id)").bindparams(id=lock_id))
        count = await self._agents.count_active(project_id)
        if count >= _AGENT_CAP_PER_PROJECT:
            raise AgentCapExceeded(f"project {project_id} has {count} agents (cap={_AGENT_CAP_PER_PROJECT})")

        if draft.name is None or not draft.name.strip():
            raise ValueError("name is required")
        if draft.model_hint is None:
            raise ValueError("model_hint is required")
        if draft.key_group_id is None:
            raise ValueError("key_group_id is required")

        await self._assert_key_group_in_project(
            key_group_id=draft.key_group_id,
            project_id=project_id,
        )
        if draft.rag_config_id is not None:
            await self._assert_rag_config_in_project(
                rag_config_id=draft.rag_config_id,
                project_id=project_id,
            )
        if draft.graphrag_config_id is not None:
            await self._assert_graphrag_config_in_project(
                graphrag_config_id=draft.graphrag_config_id,
                project_id=project_id,
            )

        wakeup = draft.wakeup_config or {}
        agent = await self._agents.create(
            project_id=project_id,
            name=draft.name.strip(),
            model_hint=draft.model_hint,
            model_id=draft.model_id,
            key_group_id=draft.key_group_id,
            system_prompt=draft.system_prompt or "",
            prompt_strategy=draft.prompt_strategy or PromptStrategy.FULL,
            rag_config_id=draft.rag_config_id,
            graphrag_config_id=draft.graphrag_config_id,
            context_mode=draft.context_mode or ContextMode.GENERAL,
            context_token_cap=draft.context_token_cap,
            a2a_enabled=bool(draft.a2a_enabled) if draft.a2a_enabled is not None else False,
            wakeup_config=wakeup,
            wakeup_authored_snapshot=wakeup if wakeup else None,
            workflow_capabilities=draft.workflow_capabilities or {},
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent",
                resource_id=agent.id,
                metadata={
                    "project_id": str(project_id),
                    "name": agent.name,
                    "model_hint": agent.model_hint.value,
                    "prompt_strategy": agent.prompt_strategy.value,
                    "context_mode": agent.context_mode.value,
                },
                request_id=request_id,
            ),
        )
        return agent

    async def get(self, agent_id: uuid.UUID) -> Agent:
        agent = await self._agents.get(agent_id)
        if agent is None:
            raise AgentNotFound(str(agent_id))
        return agent

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[Agent]:
        return await self._agents.list_for_project(
            project_id, limit=limit, offset=offset,
        )

    async def patch(
        self,
        *,
        agent_id: uuid.UUID,
        draft: AgentDraft,
        expected_version: int,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Agent:
        current = await self.get(agent_id)

        # If the key group is being swapped, validate project membership.
        new_kg = draft.key_group_id
        if new_kg is not None and new_kg != current.key_group_id:
            await self._assert_key_group_in_project(
                key_group_id=new_kg,
                project_id=current.project_id,
            )
        # SEC-H1 — same project guard when (re)attaching a RAG / GraphRAG
        # config. `clear_*` wins over a stale id, so only validate an actual
        # attach (the create path does the same check).
        if not draft.clear_rag_config and draft.rag_config_id is not None:
            await self._assert_rag_config_in_project(
                rag_config_id=draft.rag_config_id,
                project_id=current.project_id,
            )
        if not draft.clear_graphrag_config and draft.graphrag_config_id is not None:
            await self._assert_graphrag_config_in_project(
                graphrag_config_id=draft.graphrag_config_id,
                project_id=current.project_id,
            )

        values: dict[str, Any] = {}
        if draft.name is not None:
            values["name"] = draft.name.strip()
        if draft.model_hint is not None:
            values["model_hint"] = draft.model_hint.value
        if draft.clear_model_id:
            values["model_id"] = None
        elif draft.model_id is not None:
            values["model_id"] = draft.model_id
        if draft.key_group_id is not None:
            values["key_group_id"] = draft.key_group_id
        if draft.system_prompt is not None:
            values["system_prompt"] = draft.system_prompt
        if draft.prompt_strategy is not None:
            values["prompt_strategy"] = draft.prompt_strategy.value
        if draft.clear_rag_config:
            values["rag_config_id"] = None
        elif draft.rag_config_id is not None:
            values["rag_config_id"] = draft.rag_config_id
        if draft.clear_graphrag_config:
            values["graphrag_config_id"] = None
        elif draft.graphrag_config_id is not None:
            values["graphrag_config_id"] = draft.graphrag_config_id
        if draft.context_mode is not None:
            values["context_mode"] = draft.context_mode.value
        if draft.clear_context_token_cap:
            values["context_token_cap"] = None
        elif draft.context_token_cap is not None:
            values["context_token_cap"] = draft.context_token_cap
        if draft.a2a_enabled is not None:
            values["a2a_enabled"] = draft.a2a_enabled
        if draft.wakeup_config is not None:
            values["wakeup_config"] = draft.wakeup_config
            # Human edit → update the authored snapshot (G.5).
            # System actor (uuid(int=0)) updates are self-modifications
            # and should NOT overwrite the authored snapshot.
            if actor_user_id != _SYSTEM_ACTOR_ID:
                values["wakeup_authored_snapshot"] = draft.wakeup_config if draft.wakeup_config else None
        if draft.workflow_capabilities is not None:
            values["workflow_capabilities"] = draft.workflow_capabilities

        updated = await self._agents.patch(
            agent_id=agent_id,
            expected_version=expected_version,
            values=values,
        )
        if not values:
            # DOM-9: an empty patch set no recognised fields — repo.patch
            # validated the version and returned the row unchanged, with no
            # UPDATE and no version bump. Mirror ChatroomService.patch: emit
            # no `agent.edited` audit row, since nothing was actually edited.
            return updated
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.edited",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent",
                resource_id=updated.id,
                metadata={"fields": sorted(values.keys())},
                request_id=request_id,
            ),
        )
        return updated

    async def soft_delete(
        self,
        *,
        agent_id: uuid.UUID,
        expected_version: int,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._agents.soft_delete(
            agent_id=agent_id,
            expected_version=expected_version,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent",
                resource_id=agent_id,
                request_id=request_id,
            ),
        )

    # ------------------------------------------------------------------
    # MCP bindings (partial — full surface lands in E.9).
    # ------------------------------------------------------------------

    async def list_mcp_bindings(self, agent_id: uuid.UUID) -> Sequence[McpBinding]:
        await self.get(agent_id)  # existence guard
        return await self._bindings.list(agent_id)

    async def add_mcp_binding(
        self,
        *,
        agent_id: uuid.UUID,
        source: McpSource,
        reference: str,
        allowed_tools: Sequence[str],
        config: dict[str, Any],
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> McpBinding:
        await self.get(agent_id)
        binding = await self._bindings.add(
            agent_id=agent_id,
            source=source,
            reference=reference,
            allowed_tools=allowed_tools,
            config=config,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.mcp_binding_added",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_mcp_binding",
                resource_id=binding.id,
                metadata={
                    "agent_id": str(agent_id),
                    "source": source.value,
                    "reference": reference,
                    "allowed_tools": list(allowed_tools),
                },
                request_id=request_id,
            ),
        )
        return binding

    async def patch_mcp_binding(
        self,
        *,
        agent_id: uuid.UUID,
        binding_id: uuid.UUID,
        allowed_tools: Sequence[str] | None,
        config: dict[str, Any] | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> McpBinding:
        await self.get(agent_id)
        binding = await self._bindings.patch(
            agent_id=agent_id,
            binding_id=binding_id,
            allowed_tools=allowed_tools,
            config=config,
        )
        changed: list[str] = []
        if allowed_tools is not None:
            changed.append("allowed_tools")
        if config is not None:
            changed.append("config")
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.mcp_binding_updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_mcp_binding",
                resource_id=binding_id,
                metadata={
                    "agent_id": str(agent_id),
                    "fields": changed,
                },
                request_id=request_id,
            ),
        )
        return binding

    async def remove_mcp_binding(
        self,
        *,
        agent_id: uuid.UUID,
        binding_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self.get(agent_id)
        await self._bindings.remove(agent_id=agent_id, binding_id=binding_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.mcp_binding_removed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_mcp_binding",
                resource_id=binding_id,
                metadata={"agent_id": str(agent_id)},
                request_id=request_id,
            ),
        )


__all__ = ["AgentService"]
