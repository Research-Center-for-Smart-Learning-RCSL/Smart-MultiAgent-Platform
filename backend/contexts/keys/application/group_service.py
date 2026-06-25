"""Key Group use-cases (D.6 / §22.5).

The service owns:

- Capability fitness (`llm_chat` only, §7.4 rider).
- Priority bookkeeping (append at `max+1` on add; atomic reorder on PATCH).
- Authorisation assertions delegated to the HTTP layer; here we only confirm
  the key is actually carried into the group's project before accepting the
  add. Without that check a Project Owner could silently add a member's key
  that is not yet carried — violating R7.04.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.domain.errors import GroupMemberConflict, GroupWrongProject, KeyNotFound
from contexts.keys.domain.groups import (
    HourlyLimits,
    KeyGroup,
    KeyGroupMember,
    RotationPolicy,
)
from contexts.keys.domain.providers import (
    ApiKeyProvider,
    CapabilityRequirement,
    ProviderCapability,
    assert_capability,
)
from contexts.keys.infrastructure.carry_repository import KeyProjectRepository
from contexts.keys.infrastructure.group_repository import (
    KeyGroupMemberRepository,
    KeyGroupRepository,
)
from contexts.keys.infrastructure.repositories import ApiKeyRepository
from shared_kernel import audit

_LLM_CHAT = CapabilityRequirement(capability=ProviderCapability.LLM_CHAT)




class KeyGroupService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._groups = KeyGroupRepository(db)
        self._members = KeyGroupMemberRepository(db)
        self._keys = ApiKeyRepository(db)
        self._carries = KeyProjectRepository(db)

    # ----- groups --------------------------------------------------------

    async def list_for_project(self, project_id: uuid.UUID) -> list[KeyGroup]:
        return await self._groups.list_for_project(project_id)

    async def list_for_project_with_counts(
        self, project_id: uuid.UUID
    ) -> list[tuple[KeyGroup, int]]:
        return await self._groups.list_for_project_with_counts(project_id)

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        name: str,
        actor_user_id: uuid.UUID,
        request_id: uuid.UUID | None = None,
    ) -> KeyGroup:
        group = await self._groups.create(project_id=project_id, name=name)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="key_group.created",
                actor_user_id=actor_user_id,
                resource_type="key_group",
                resource_id=group.id,
                metadata={"project_id": str(project_id), "name": name},
                request_id=request_id,
            ),
        )
        return group

    async def get_with_members(self, group_id: uuid.UUID) -> tuple[KeyGroup, list[KeyGroupMember]] | None:
        group = await self._groups.get_active(group_id)
        if group is None:
            return None
        members = await self._members.list_ordered(group_id)
        return group, members

    async def rename(
        self,
        *,
        group_id: uuid.UUID,
        name: str,
        actor_user_id: uuid.UUID,
        request_id: uuid.UUID | None = None,
    ) -> None:
        if await self._groups.get_active(group_id) is None:
            raise KeyNotFound(str(group_id))
        await self._groups.rename(group_id, name)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="key_group.renamed",
                actor_user_id=actor_user_id,
                resource_type="key_group",
                resource_id=group_id,
                metadata={"name": name},
                request_id=request_id,
            ),
        )

    async def delete(
        self,
        *,
        group_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request_id: uuid.UUID | None = None,
    ) -> None:
        if await self._groups.get_active(group_id) is None:
            raise KeyNotFound(str(group_id))
        await self._groups.soft_delete(group_id, at=datetime.now(tz=UTC))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="key_group.deleted",
                actor_user_id=actor_user_id,
                resource_type="key_group",
                resource_id=group_id,
                request_id=request_id,
            ),
        )

    # ----- members -------------------------------------------------------

    async def add_member(
        self,
        *,
        group_id: uuid.UUID,
        key_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request_id: uuid.UUID | None = None,
    ) -> KeyGroupMember:
        group = await self._groups.get_active(group_id)
        if group is None:
            raise KeyNotFound(str(group_id))
        key = await self._keys.get_active(key_id)
        if key is None:
            raise KeyNotFound(str(key_id))

        # R7.01 — only llm_chat-capable providers may join a group.
        assert_capability(ApiKeyProvider(key.provider), _LLM_CHAT)

        # R7.04 — the key must already be carried into the group's project.
        carried = await self._carries.list_active_in_project(group.project_id)
        if not any(c.id == key_id for c in carried):
            raise GroupWrongProject(f"key {key_id} is not carried into project {group.project_id}")

        priority = await self._members.next_priority(group_id)
        try:
            await self._members.add(group_id=group_id, key_id=key_id, priority=priority)
        except IntegrityError as exc:
            if hasattr(exc.orig, "pgcode") and exc.orig.pgcode == "23505":
                raise GroupMemberConflict(f"key {key_id} already in group {group_id}") from None
            raise

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="key_group.member_added",
                actor_user_id=actor_user_id,
                resource_type="key_group",
                resource_id=group_id,
                metadata={
                    "key_id": str(key_id),
                    "priority": priority,
                    "provider": key.provider.value,
                },
                request_id=request_id,
            ),
        )
        return KeyGroupMember(
            group_id=group_id,
            key_id=key_id,
            priority=priority,
            rotation=RotationPolicy(),
            limits=HourlyLimits(),
        )

    async def patch_member(
        self,
        *,
        group_id: uuid.UUID,
        key_id: uuid.UUID,
        col_updates: dict[str, Any],
        actor_user_id: uuid.UUID,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._members.patch(group_id=group_id, key_id=key_id, updates=col_updates)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="key_group.member_patched",
                actor_user_id=actor_user_id,
                resource_type="key_group",
                resource_id=group_id,
                metadata={"key_id": str(key_id), "fields": sorted(col_updates.keys())},
                request_id=request_id,
            ),
        )

    async def remove_member(
        self,
        *,
        group_id: uuid.UUID,
        key_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request_id: uuid.UUID | None = None,
    ) -> None:
        removed = await self._members.remove(group_id=group_id, key_id=key_id)
        if not removed:
            raise KeyNotFound(f"member {key_id} in group {group_id}")
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="key_group.member_removed",
                actor_user_id=actor_user_id,
                resource_type="key_group",
                resource_id=group_id,
                metadata={"key_id": str(key_id)},
                request_id=request_id,
            ),
        )

    async def reorder(
        self,
        *,
        group_id: uuid.UUID,
        priorities: dict[uuid.UUID, int],
        actor_user_id: uuid.UUID,
        request_id: uuid.UUID | None = None,
    ) -> None:
        """Atomically re-number member priorities (§22.5 reorder)."""
        if await self._groups.get_active(group_id) is None:
            raise KeyNotFound(str(group_id))
        await self._members.reorder_atomic(group_id=group_id, priorities=priorities)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="key_group.reordered",
                actor_user_id=actor_user_id,
                resource_type="key_group",
                resource_id=group_id,
                metadata={
                    "priorities": {str(k): v for k, v in priorities.items()},
                },
                request_id=request_id,
            ),
        )


__all__ = ["KeyGroupService"]
