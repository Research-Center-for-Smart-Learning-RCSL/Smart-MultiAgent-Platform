"""Facilitator-controlled room-level activity activation lifecycle."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.application.ports import ActivityActivationRepository, ActivityTypeReader
from contexts.activities.domain.errors import (
    ActivityActivationNotFound,
    ActivityAlreadyActive,
    ActivityTypeNotFound,
)
from contexts.activities.domain.models import ActivityActivation, ActivityActivationEndResult
from shared_kernel import audit


class ActivationService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        activation_repo: ActivityActivationRepository,
        type_repo: ActivityTypeReader,
    ) -> None:
        self._db = db
        self._repo = activation_repo
        self._type_repo = type_repo

    async def start(
        self,
        *,
        project_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        started_by_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivityActivation:
        activity_type = await self._type_repo.get(activity_type_id)
        if activity_type is None or activity_type.project_id != project_id:
            raise ActivityTypeNotFound(str(activity_type_id))

        activation_id = await self._repo.create_active(
            chatroom_id=chatroom_id,
            activity_type_id=activity_type_id,
            started_by_user_id=started_by_user_id,
        )
        if activation_id is None:
            active = await self._repo.get_active(chatroom_id)
            if active is None:  # pragma: no cover - a partial-unique conflict has a winner
                raise ActivityActivationNotFound(str(chatroom_id))
            if active.activity_type_id != activity_type_id:
                raise ActivityAlreadyActive(str(active.id))
            return active

        activation = await self._repo.get(activation_id)
        if activation is None:  # pragma: no cover - inserted in this transaction
            raise ActivityActivationNotFound(str(activation_id))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="activity.activation_started",
                actor_user_id=started_by_user_id,
                actor_ip=actor_ip,
                resource_type="activity_activation",
                resource_id=activation.id,
                metadata={"chatroom_id": str(chatroom_id), "activity_type_id": str(activity_type_id)},
                request_id=request_id,
            ),
        )
        return activation

    async def end(
        self,
        *,
        chatroom_id: uuid.UUID,
        activation_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivityActivationEndResult:
        activation = await self._repo.get(activation_id)
        if activation is None or activation.chatroom_id != chatroom_id:
            raise ActivityActivationNotFound(str(activation_id))
        if await self._repo.end(activation_id):
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="activity.activation_ended",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="activity_activation",
                    resource_id=activation_id,
                    metadata={
                        "chatroom_id": str(chatroom_id),
                        "activity_type_id": str(activation.activity_type_id),
                    },
                    request_id=request_id,
                ),
            )
            updated = await self._repo.get(activation_id)
            if updated is not None:
                return ActivityActivationEndResult(activation=updated, transitioned=True)
        return ActivityActivationEndResult(activation=activation, transitioned=False)

    async def get_active(self, chatroom_id: uuid.UUID) -> ActivityActivation | None:
        return await self._repo.get_active(chatroom_id)


__all__ = ["ActivationService"]
