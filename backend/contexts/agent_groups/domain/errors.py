"""Agent-groups domain errors → RFC 7807 slugs (Phase 2b WS2)."""

from __future__ import annotations


class AgentGroupError(Exception):
    code: str = "agent-groups/generic"


class AgentGroupNotFound(AgentGroupError):
    code = "agent-groups/not-found"


class AgentGroupNameConflict(AgentGroupError):
    """Another active group in the project already uses this name."""

    code = "agent-groups/name-conflict"


class AgentGroupMemberProjectMismatch(AgentGroupError):
    """An agent being added lives in a different project than the group.

    Group membership is a per-project trust boundary (it gates who contributes
    to and reads a shared Concept Map), so a cross-project member is rejected.
    """

    code = "agent-groups/member-project-mismatch"


class AgentGroupMemberNotFound(AgentGroupError):
    """The agent named in a member-scoped request is not a live member of the group."""

    code = "agent-groups/member-not-found"


class AgentGroupReadMembersInvalid(AgentGroupError):
    """A read filter names a non-member, or is an empty list.

    An empty list would read nothing; "the whole group" is ``null``. Only the
    removal of a member may leave a list empty, so the owner sees it as a
    deliberate state rather than one a request created.
    """

    code = "agent-groups/invalid-read-members"


__all__ = [
    "AgentGroupError",
    "AgentGroupMemberNotFound",
    "AgentGroupMemberProjectMismatch",
    "AgentGroupNameConflict",
    "AgentGroupNotFound",
    "AgentGroupReadMembersInvalid",
]
