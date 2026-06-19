"""Sub-agent lifecycle service (G.8 — R15.18–R15.23).

Manages spawning, destroying, and tracking sub-agents with:
- Depth exactly 1 (no sub-sub-agents).
- Concurrent cap (default 3, hard cap 20).
- Inheritance rules per R15.22.
- Usage attribution via parent_agent_id (R15.23).

SoC:
- Domain models/constants → ``domain.models``
- DB access → ``infrastructure.repositories.AgentInstanceRepository``
- Agent data → ``AgentsFacade``
- Audit → ``shared_kernel.audit``
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.domain.models import Agent
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.orchestration.domain.errors import (
    SubagentConcurrencyExceeded,
    SubagentDepthExceeded,
)
from contexts.orchestration.domain.models import (
    SUBAGENT_MAX_CONCURRENT_DEFAULT,
    SUBAGENT_MAX_CONCURRENT_HARD,
    AgentInstance,
)
from contexts.orchestration.infrastructure.metrics import SUBAGENT_CONCURRENCY
from contexts.orchestration.infrastructure.repositories import AgentInstanceRepository
from shared_kernel import audit

logger = logging.getLogger(__name__)


class SubagentService:
    """Application-level sub-agent lifecycle manager (G.8)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._instances = AgentInstanceRepository(db)
        self._agents = AgentsFacade(db)

    # ------------------------------------------------------------------
    # Workflow root instance (ASYNC-3)
    # ------------------------------------------------------------------

    async def ensure_root_instance(
        self,
        *,
        agent_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
    ) -> AgentInstance:
        """Get-or-create the synthetic root instance for a workflow run.

        A workflow ``subagent_spawn`` node spawns subagents but has no agent
        *instance* to parent them under — only an agent *definition*. This
        creates (once per workflow run, then reuses) a depth-0 root instance
        so the depth and concurrency invariants enforced by :meth:`spawn`
        hold unchanged.

        Two parallel ``subagent_spawn`` nodes in the same run could each lose
        the get-or-create race and create their own root; the only effect is
        that the concurrency cap is then counted per-root rather than
        per-run, which is acceptable for this interim wiring.
        """
        existing = await self._instances.find_alive_root_for_workflow_run(
            agent_id=agent_id,
            workflow_run_id=workflow_run_id,
        )
        if existing is not None:
            return existing
        return await self._instances.insert(
            id=uuid.uuid4(),
            agent_id=agent_id,
            parent_id=None,
            chatroom_id=None,
            run_context={
                "synthetic_root": True,
                "workflow_run_id": str(workflow_run_id),
            },
            task_description=f"workflow run {workflow_run_id} subagent root",
        )

    # ------------------------------------------------------------------
    # Spawn
    # ------------------------------------------------------------------

    async def spawn(
        self,
        *,
        parent_instance_id: uuid.UUID,
        parent_agent_id: uuid.UUID,
        task_description: str,
        chatroom_id: uuid.UUID | None = None,
        max_concurrent: int = SUBAGENT_MAX_CONCURRENT_DEFAULT,
    ) -> AgentInstance:
        """Spawn a sub-agent from a parent agent instance.

        Raises SubagentDepthExceeded if the parent is itself a sub-agent.
        Raises SubagentConcurrencyExceeded if the concurrent cap is hit.
        """
        parent_instance = await self._instances.get(parent_instance_id)
        if parent_instance is None:
            raise ValueError(f"parent instance {parent_instance_id} not found")

        # R15.19: depth exactly 1.
        if parent_instance.parent_id is not None:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="subagent.depth_exceeded",
                    resource_type="agent_instance",
                    resource_id=parent_instance_id,
                    metadata={
                        "parent_agent_id": str(parent_agent_id),
                        "reason": "sub-agent cannot spawn sub-agents",
                    },
                ),
            )
            raise SubagentDepthExceeded(
                f"instance {parent_instance_id} is already a sub-agent; " f"depth > 1 not allowed"
            )

        # R15.20: concurrent cap.
        effective_cap = min(max_concurrent, SUBAGENT_MAX_CONCURRENT_HARD)
        alive = await self._instances.count_alive_children(parent_instance_id)
        if alive >= effective_cap:
            raise SubagentConcurrencyExceeded(
                f"parent {parent_instance_id} has {alive} alive sub-agents " f"(max {effective_cap})"
            )

        # Build inherited run_context per R15.22.
        parent_agent = await self._agents.get_agent(parent_agent_id)
        if parent_agent is None:
            raise ValueError(f"parent agent {parent_agent_id} not found")

        run_context = self._build_inherited_context(parent_agent, task_description)

        child_id = uuid.uuid4()
        child = await self._instances.insert(
            id=child_id,
            agent_id=parent_agent_id,
            parent_id=parent_instance_id,
            chatroom_id=chatroom_id,
            run_context=run_context,
            task_description=task_description,
        )

        SUBAGENT_CONCURRENCY.labels(parent=str(parent_agent_id)).inc()

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="subagent.spawned",
                resource_type="agent_instance",
                resource_id=child_id,
                metadata={
                    "parent_instance_id": str(parent_instance_id),
                    "parent_agent_id": str(parent_agent_id),
                    "task_description": task_description[:200],
                    "alive_count": alive + 1,
                },
            ),
        )

        return child

    # ------------------------------------------------------------------
    # Destroy
    # ------------------------------------------------------------------

    async def destroy(
        self,
        instance_id: uuid.UUID,
        *,
        state: str = "completed",
    ) -> None:
        instance = await self._instances.get(instance_id)
        if instance is None:
            return

        await self._instances.destroy(instance_id, state=state)

        if instance.parent_id:
            SUBAGENT_CONCURRENCY.labels(
                parent=str(instance.agent_id),
            ).dec()

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="subagent.destroyed",
                resource_type="agent_instance",
                resource_id=instance_id,
                metadata={
                    "state": state,
                    "parent_id": str(instance.parent_id) if instance.parent_id else None,
                    "agent_id": str(instance.agent_id),
                },
            ),
        )

        # ASYNC-3: if a workflow run parked itself waiting on this subagent,
        # wake it now that the instance has finished.
        await self._fire_workflow_callback(instance_id)

    async def _fire_workflow_callback(self, instance_id: uuid.UUID) -> None:
        """Resume a workflow run parked on this subagent (W10 completion hook).

        ``subagent_spawn`` stores ``wf:subagent_callback:{instance_id}`` in
        Redis when it parks a run with ``wait_for_all``. On destruction we read
        that marker and enqueue ``workflow_subagent_complete`` so the engine
        resumes the run at the stored output port. Instances without the
        marker (e.g. chatroom subagents) are a no-op.

        The enqueue happens inside the caller's transaction; ``workflow_
        subagent_complete`` only touches workflow tables, independent of the
        ``agent_instances`` row being destroyed, so there is no read-after-
        write hazard if that transaction is still uncommitted.
        """
        from shared_kernel.auth.clients import get_redis

        redis = get_redis()
        key = f"wf:subagent_callback:{instance_id}"
        raw = await redis.get(key)
        if not raw:
            return
        try:
            callback = json.loads(raw)
            run_id = callback["run_id"]
            node_id = callback["node_id"]
            port = callback.get("port", "success")
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("subagent callback for %s is malformed: %r", instance_id, raw)
            await redis.delete(key)
            return

        from shared_kernel.queue import enqueue

        await enqueue("workflow_subagent_complete", run_id, node_id, port)
        await redis.delete(key)

    # ------------------------------------------------------------------
    # Inheritance (R15.22)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_inherited_context(
        parent: Agent,
        task_description: str,
    ) -> dict[str, Any]:
        """Build the run_context for a sub-agent per R15.22."""
        return {
            "key_group_id": str(parent.key_group_id),
            "system_prompt": parent.system_prompt,
            "prompt_strategy": parent.prompt_strategy.value,
            "model_hint": parent.model_hint.value,
            "a2a_enabled": False,
            "mcp_servers": True,  # inherited, actual bindings resolved at runtime
            "rag_config_id": None,
            "graphrag_config_id": None,
            "context_mode": parent.context_mode.value,
            "context_token_cap": parent.context_token_cap,
            "wakeup_config": None,
            "can_create_subagent": False,
            "can_instruct": False,
            "can_approve": False,
            "task_description": task_description,
            "parent_agent_id": str(parent.id),
        }

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    async def get_instance(self, instance_id: uuid.UUID) -> AgentInstance | None:
        return await self._instances.get(instance_id)

    async def resolve_project(self, instance_id: uuid.UUID) -> uuid.UUID | None:
        """Project owning an instance (via its agent) — authz helper (API-2).

        Returns None when the instance or its agent no longer exists.
        """
        instance = await self._instances.get(instance_id)
        if instance is None:
            return None
        agent = await self._agents.get_agent(instance.agent_id, include_deleted=True)
        return agent.project_id if agent else None

    async def list_children(self, parent_id: uuid.UUID) -> list[AgentInstance]:
        return await self._instances.list_alive_children(parent_id)

    async def cleanup_expired(self, retention_days: int = 30) -> int:
        return await self._instances.delete_older_than_days(retention_days)


__all__ = ["SubagentService"]
