"""Agent-groups domain errors → RFC 7807 slugs (Phase 2b WS2)."""

from __future__ import annotations


class AgentGroupError(Exception):
    code: str = "agent-groups/generic"


class AgentGroupNotFound(AgentGroupError):
    code = "agent-groups/not-found"


class AgentGroupMemberProjectMismatch(AgentGroupError):
    """An agent being added lives in a different project than the group.

    Group membership is a per-project trust boundary (it gates who contributes
    to and reads a shared Concept Map), so a cross-project member is rejected.
    """

    code = "agent-groups/member-project-mismatch"


__all__ = [
    "AgentGroupError",
    "AgentGroupMemberProjectMismatch",
    "AgentGroupNotFound",
]
