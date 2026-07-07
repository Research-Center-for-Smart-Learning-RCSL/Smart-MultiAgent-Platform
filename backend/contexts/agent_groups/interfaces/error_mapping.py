"""Agent-groups domain errors → RFC 7807 registration (Phase 2b WS2).

Dispatch + fallback live in ``shared_kernel.errors.context_handler`` (API-3).
"""

from __future__ import annotations

from fastapi import FastAPI

from contexts.agent_groups.domain import errors
from shared_kernel.errors.context_handler import ErrorMap, register_context_handler

_MAP: ErrorMap = {
    errors.AgentGroupNotFound: (
        "agent-groups/not-found",
        404,
        "Agent group not found",
    ),
    errors.AgentGroupMemberProjectMismatch: (
        "agent-groups/member-project-mismatch",
        422,
        "Agent does not belong to the group's project",
    ),
    errors.AgentGroupNameConflict: (
        "agent-groups/name-conflict",
        409,
        "An agent group with this name already exists in the project",
    ),
}


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.AgentGroupError, _MAP)


__all__ = ["register"]
