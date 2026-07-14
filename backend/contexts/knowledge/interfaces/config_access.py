"""Owner-aware read/subscribe authorization for knowledge configs (R11.17).

One predicate, shared by the GraphRAG REST reads, the config-scoped WebSocket
handshake, and the mid-socket re-auth callback, so every surface enforces the
same rule and cannot drift:

- A chatroom-owned Concept Map inherits the owning room's ACL (§21.1): only
  principals permitted to read the private room may read its graph or subscribe
  to its build status.
- agent_group- and workspace-owned Concept Maps require project membership plus
  the owner's ``concept_map_enabled`` opt-in (set only by a strict Project
  Owner, [R11.17]).
- RAG and Knowledge-Map configs (no ``owner_kind``) require project membership.

Admin bypasses. SoC: this consumes the conversation room-ACL port, the tenancy
role resolver, and the agent_groups / conversation facades — it never
re-implements room-flag evaluation nor reaches into another context's tables.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agent_groups.interfaces.facade import AgentGroupFacade
from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    ForbiddenInRoom,
    WorkspaceNotFound,
)
from contexts.conversation.interfaces.access import ensure_can_read, resolve_room_access
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver
from shared_kernel.auth.permissions import Principal, Scope


async def has_config_read_access(
    session: AsyncSession,
    *,
    principal: Principal,
    cfg: Any,
) -> bool:
    """Return whether *principal* may read / subscribe to knowledge config *cfg*.

    Fail-closed: a deleted room, a missing owner, or a disabled ``concept_map_enabled``
    opt-in all yield ``False``. A store error propagates to the caller (the WS
    watchdog retries the next window rather than dropping a live socket).
    """
    if principal.is_admin:
        return True

    owner_kind = getattr(cfg, "owner_kind", None)

    if owner_kind == "chatroom":
        try:
            access = await resolve_room_access(
                session, principal=principal, chatroom_id=cfg.owner_chatroom_id
            )
            ensure_can_read(access, is_admin=False)  # admin handled above
        except (ChatroomNotFound, WorkspaceNotFound, ForbiddenInRoom):
            return False
        return True

    # agent_group / workspace / rag / knowmap -> project membership first.
    resolver = TenancyRoleResolver(session)
    roles = await resolver.roles_for(principal, Scope(project_id=cfg.project_id))
    if not roles:
        return False

    # R11.17: agent_group / workspace Concept Maps additionally require the
    # owner's concept_map_enabled opt-in.
    if owner_kind == "agent_group":
        group = await AgentGroupFacade(session).get_group(cfg.owner_agent_group_id)
        return group is not None and group.concept_map_enabled
    if owner_kind == "workspace":
        workspace = await ConversationFacade(session).get_workspace(cfg.owner_workspace_id)
        return workspace is not None and workspace.concept_map_enabled

    # RAG / Knowledge-Map configs: project membership suffices.
    return True


__all__ = ["has_config_read_access"]
