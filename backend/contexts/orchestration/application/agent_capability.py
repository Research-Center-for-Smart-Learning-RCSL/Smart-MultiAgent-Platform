"""Shared ``workflow_capabilities`` predicate (R15.10a, R15.18).

One predicate so ``ApprovalService`` and ``InstructService`` cannot
independently drift on what counts as "capable" (workflow-capability-
enforcement spec, R3).
"""

from __future__ import annotations

from contexts.agents.interfaces.facade import Agent


def agent_has_capability(agent: Agent | None, capability: str) -> bool:
    """Fail-closed: a missing or soft-deleted agent (``None``) has no capability."""
    if agent is None:
        return False
    return bool(agent.workflow_capabilities.get(capability, False))


__all__ = ["agent_has_capability"]
