"""Workspace use-cases (§22.10, R13.01).

Creating a workspace also creates one default Chat Room atomically so the
R13.02 invariant "every workspace has ≥ 1 chat room" holds at commit time.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import WorkspaceNotFound
from contexts.conversation.domain.models import Chatroom, Workspace
from contexts.conversation.infrastructure.repositories import (
    ChatroomRepository,
    WorkspaceRepository,
)
from shared_kernel import audit


@dataclass(frozen=True, slots=True)
class WorkspaceWithDefaultRoom:
    workspace: Workspace
    default_chatroom: Chatroom


class WorkspaceService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._workspaces = WorkspaceRepository(db)
        self._chatrooms = ChatroomRepository(db)

    async def list_for_project(
        self,
        project_id: uuid.UUID,
    ) -> Sequence[Workspace]:
        return await self._workspaces.list_for_project(project_id)

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        name: str,
        default_room_name: str = "general",
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> WorkspaceWithDefaultRoom:
        workspace = await self._workspaces.create(
            project_id=project_id,
            name=name,
        )
        default_room = await self._chatrooms.create(
            workspace_id=workspace.id,
            name=default_room_name,
            created_by_user_id=actor_user_id,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="workspace.created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="workspace",
                resource_id=workspace.id,
                metadata={
                    "project_id": str(project_id),
                    "name": name,
                    "default_chatroom_id": str(default_room.id),
                },
                request_id=request_id,
            ),
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
                    "workspace_id": str(workspace.id),
                    "auto_created": True,
                },
                request_id=request_id,
            ),
        )
        return WorkspaceWithDefaultRoom(
            workspace=workspace,
            default_chatroom=default_room,
        )

    async def set_concept_map_enabled(
        self,
        *,
        workspace_id: uuid.UUID,
        enabled: bool,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        """Toggle the workspace's Concept Map privacy opt-in and audit it (R11.10).

        Enabling exposes the shared workspace map to retrieval, so the route
        restricts this to a strict Project Owner; the change is audit-logged
        unconditionally.
        """
        existing = await self._workspaces.get(workspace_id)
        if existing is None:
            raise WorkspaceNotFound(str(workspace_id))
        updated = await self._workspaces.set_concept_map_enabled(workspace_id=workspace_id, enabled=enabled)
        if not updated:
            # Concurrently soft-deleted between the existence check and the write.
            raise WorkspaceNotFound(str(workspace_id))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="workspace.concept_map_toggled",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="workspace",
                resource_id=workspace_id,
                metadata={
                    "project_id": str(existing.project_id),
                    "concept_map_enabled": enabled,
                },
                request_id=request_id,
            ),
        )

    async def soft_delete(
        self,
        *,
        workspace_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        existing = await self._workspaces.get(workspace_id)
        if existing is None:
            raise WorkspaceNotFound(str(workspace_id))
        await self._workspaces.soft_delete(workspace_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="workspace.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="workspace",
                resource_id=workspace_id,
                request_id=request_id,
            ),
        )


__all__ = ["WorkspaceService", "WorkspaceWithDefaultRoom"]
