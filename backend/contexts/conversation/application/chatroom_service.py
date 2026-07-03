"""Chat-room use-cases (§22.10, R13.02 / R13.04 / R13.05)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import ChatroomNotFound
from contexts.conversation.domain.models import (
    Chatroom,
    ChatroomAgent,
    ChatroomAgentRole,
    ChatroomGuest,
)
from contexts.conversation.infrastructure.repositories import (
    ChatroomAgentRepository,
    ChatroomGuestRepository,
    ChatroomRepository,
    WorkspaceRepository,
)
from shared_kernel import audit


@dataclass(frozen=True, slots=True)
class ChatroomFlagsPatch:
    name: str | None = None
    allow_org_members: bool | None = None
    allow_project_members: bool | None = None
    allow_project_owners_only: bool | None = None
    allow_guest_links: bool | None = None
    disclose_observers: bool | None = None


class ChatroomService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._rooms = ChatroomRepository(db)
        self._workspaces = WorkspaceRepository(db)
        self._agents = ChatroomAgentRepository(db)
        self._guests = ChatroomGuestRepository(db)

    # ---- queries ---------------------------------------------------------

    async def get(self, chatroom_id: uuid.UUID) -> Chatroom:
        room = await self._rooms.get(chatroom_id)
        if room is None:
            raise ChatroomNotFound(str(chatroom_id))
        return room

    async def list_for_workspace(
        self,
        workspace_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Chatroom]:
        return await self._rooms.list_for_workspace(
            workspace_id,
            limit=limit,
            offset=offset,
        )

    async def list_agents(
        self,
        chatroom_id: uuid.UUID,
    ) -> Sequence[ChatroomAgent]:
        return await self._agents.list(chatroom_id)

    async def list_guests(
        self,
        chatroom_id: uuid.UUID,
    ) -> Sequence[ChatroomGuest]:
        return await self._guests.list(chatroom_id)

    async def rooms_with_observers(
        self,
        chatroom_ids: Sequence[uuid.UUID],
    ) -> set[uuid.UUID]:
        return await self._agents.rooms_with_observers(chatroom_ids)

    async def agent_role(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> ChatroomAgentRole | None:
        """Role of a single binding, or None when the agent is not bound.
        Used by the unbind endpoint's observer creator-gate (O-5/R28.02)."""
        return await self._agents.role_of(chatroom_id=chatroom_id, agent_id=agent_id)

    # ---- commands --------------------------------------------------------

    async def create(
        self,
        *,
        workspace_id: uuid.UUID,
        name: str,
        allow_org_members: bool = False,
        allow_project_members: bool = True,
        allow_project_owners_only: bool = False,
        allow_guest_links: bool = False,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Chatroom:
        room = await self._rooms.create(
            workspace_id=workspace_id,
            name=name,
            allow_org_members=allow_org_members,
            allow_project_members=allow_project_members,
            allow_project_owners_only=allow_project_owners_only,
            allow_guest_links=allow_guest_links,
            created_by_user_id=actor_user_id,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="chatroom.created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom",
                resource_id=room.id,
                metadata={
                    "workspace_id": str(workspace_id),
                    "name": name,
                    "flags": {
                        "allow_org_members": allow_org_members,
                        "allow_project_members": allow_project_members,
                        "allow_project_owners_only": allow_project_owners_only,
                        "allow_guest_links": allow_guest_links,
                    },
                },
                request_id=request_id,
            ),
        )
        return room

    async def patch(
        self,
        *,
        chatroom_id: uuid.UUID,
        expected_version: int,
        patch: ChatroomFlagsPatch,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Chatroom:
        values: dict[str, object] = {}
        if patch.name is not None:
            values["name"] = patch.name
        if patch.allow_org_members is not None:
            values["allow_org_members"] = patch.allow_org_members
        if patch.allow_project_members is not None:
            values["allow_project_members"] = patch.allow_project_members
        if patch.allow_project_owners_only is not None:
            values["allow_project_owners_only"] = patch.allow_project_owners_only
        if patch.allow_guest_links is not None:
            values["allow_guest_links"] = patch.allow_guest_links
        old_disclose: bool | None = None
        if patch.disclose_observers is not None:
            old_disclose = (await self.get(chatroom_id)).disclose_observers
            values["disclose_observers"] = patch.disclose_observers
        if not values:
            # Nothing to change; return existing row as-is.
            return await self.get(chatroom_id)
        room = await self._rooms.update(
            chatroom_id=chatroom_id,
            expected_version=expected_version,
            values=values,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="chatroom.updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom",
                resource_id=chatroom_id,
                metadata={"changed": list(values.keys())},
                request_id=request_id,
            ),
        )
        if patch.disclose_observers is not None and patch.disclose_observers != old_disclose:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="chatroom.disclosure_changed",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="chatroom",
                    resource_id=chatroom_id,
                    metadata={"old": old_disclose, "new": patch.disclose_observers},
                    request_id=request_id,
                ),
            )
        return room

    async def soft_delete(
        self,
        *,
        chatroom_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Chatroom | None:
        """Soft-delete. R13.02: if the workspace has no other active rooms
        left after this call, auto-create a default room so the invariant
        "every workspace has ≥ 1 chatroom" still holds."""
        room = await self._rooms.get(chatroom_id)
        if room is None:
            raise ChatroomNotFound(str(chatroom_id))
        await self._rooms.soft_delete(chatroom_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="chatroom.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom",
                resource_id=chatroom_id,
                request_id=request_id,
            ),
        )
        remaining = await self._rooms.count_active_in_workspace(room.workspace_id)
        if remaining == 0:
            default_room = await self._rooms.create(
                workspace_id=room.workspace_id,
                name="general",
                created_by_user_id=actor_user_id,
            )
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="chatroom.created",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="chatroom",
                    resource_id=default_room.id,
                    metadata={
                        "workspace_id": str(room.workspace_id),
                        "auto_created": True,
                        "reason": "last_room_deleted",
                    },
                    request_id=request_id,
                ),
            )
            return default_room
        return None

    # ---- compaction -------------------------------------------------------

    @staticmethod
    async def request_compaction(chatroom_id: uuid.UUID) -> None:
        """Record a one-shot compact intent flag (K.2) and enqueue a job.

        The next agent turn in this room reads + clears the flag and forces
        a compaction pass before its provider call.
        """
        from shared_kernel.auth.clients import get_redis
        from shared_kernel.queue import enqueue as enqueue_job

        await get_redis().set(f"compact:pending:{chatroom_id}", "1", ex=3600)
        await enqueue_job("compact_chatroom", str(chatroom_id))

    # ---- agent registry --------------------------------------------------

    async def add_agent(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        role: ChatroomAgentRole = ChatroomAgentRole.NORMAL,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._agents.add(chatroom_id=chatroom_id, agent_id=agent_id, role=role)
        action = "chatroom.observer_bound" if role is ChatroomAgentRole.OBSERVER else "chatroom.agent_added"
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom_agent",
                resource_id=chatroom_id,
                metadata={"agent_id": str(agent_id), "role": role.value},
                request_id=request_id,
            ),
        )

    # Bounded retry for the read-then-CAS-write loop below — generous relative
    # to how rarely two role-change requests for the same binding actually
    # race (a human clicking a settings dropdown), while keeping the loop
    # provably terminating.
    _SET_ROLE_MAX_ATTEMPTS = 5

    async def set_agent_role(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        role: ChatroomAgentRole,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """CAS the transition on the role actually observed (mirrors the
        observation-release CAS in observation_repo.py) so concurrent
        role-change requests can't both win and both audit-log the same
        old_role -> new_role transition when only one of them really
        happened. A losing attempt re-reads the fresh role and retries —
        it may discover the target role was already reached (no-op) or
        needs a different transition than the one it started with."""
        for _attempt in range(self._SET_ROLE_MAX_ATTEMPTS):
            old_role = await self._agents.role_of(chatroom_id=chatroom_id, agent_id=agent_id)
            if old_role is None:
                return False
            if old_role is role:
                return True
            won = await self._agents.set_role(
                chatroom_id=chatroom_id,
                agent_id=agent_id,
                expected_role=old_role,
                role=role,
            )
            if not won:
                continue  # lost the race — re-read and retry against the fresh value
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="chatroom.observer_role_changed",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="chatroom_agent",
                    resource_id=chatroom_id,
                    metadata={
                        "agent_id": str(agent_id),
                        "old_role": old_role.value,
                        "new_role": role.value,
                    },
                    request_id=request_id,
                ),
            )
            return True
        return False

    async def remove_agent(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._agents.remove(chatroom_id=chatroom_id, agent_id=agent_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="chatroom.agent_removed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom_agent",
                resource_id=chatroom_id,
                metadata={"agent_id": str(agent_id)},
                request_id=request_id,
            ),
        )


__all__ = ["ChatroomFlagsPatch", "ChatroomService"]
