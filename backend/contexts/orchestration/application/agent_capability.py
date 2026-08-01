"""Shared ``workflow_capabilities`` predicate (R15.10a, R15.18).

One predicate so ``ApprovalService`` and ``InstructService`` cannot
independently drift on what counts as "capable" (workflow-capability-
enforcement spec, R3).
"""

from __future__ import annotations

from typing import Literal

from contexts.agents.interfaces.facade import Agent

WorkflowCapability = Literal["can_instruct", "can_approve", "can_create_subagent"]


def agent_has_capability(agent: Agent | None, capability: WorkflowCapability) -> bool:
    """Fail-closed: a missing or soft-deleted agent (``None``) has no capability."""
    if agent is None:
        return False
    return bool(agent.workflow_capabilities.get(capability, False))


__all__ = ["WorkflowCapability", "agent_has_capability"]
