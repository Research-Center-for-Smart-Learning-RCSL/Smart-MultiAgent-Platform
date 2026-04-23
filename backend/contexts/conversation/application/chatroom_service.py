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
        self, workspace_id: uuid.UUID,
    ) -> Sequence[Chatroom]:
        return await self._rooms.list_for_workspace(workspace_id)

    async def list_agents(
        self, chatroom_id: uuid.UUID,
    ) -> Sequence[ChatroomAgent]:
        return await self._agents.list(chatroom_id)

    async def list_guests(
        self, chatroom_id: uuid.UUID,
    ) -> Sequence[ChatroomGuest]:
        return await self._guests.list(chatroom_id)

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
                workspace_id=room.workspace_id, name="general",
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

    # ---- agent registry --------------------------------------------------

    async def add_agent(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._agents.add(chatroom_id=chatroom_id, agent_id=agent_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="chatroom.agent_added",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom_agent",
                resource_id=chatroom_id,
                metadata={"agent_id": str(agent_id)},
                request_id=request_id,
            ),
        )

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
