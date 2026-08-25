"""Observer-agent observation use-cases (SRS §28).

Observations are the private output channel of observer-role bindings
(R28.01/R28.03): stored out-of-band in ``agent_observations``, readable only
by the room creator, and copied into the room (or into selected agents'
``pending_notify`` queues) only through an explicit, audited *release*
(R28.06/R28.07).

AuthZ note: route handlers gate every call here with ``resolve_room_access``
+ ``ensure_room_creator`` (except ``record``, which is worker-side). This
service owns the domain rules: target validation, the single-shot release
CAS, the room-message insert, and audit.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import (
    InvalidReleaseTarget,
    ObservationAlreadyReleased,
    ObservationNotFound,
)
from contexts.conversation.domain.models import (
    AgentObservation,
    ChatroomAgentRole,
    Message,
    SenderType,
)
from contexts.conversation.infrastructure.repositories import (
    ChatroomAgentRepository,
    ChatroomRepository,
    MessageRepository,
    ObservationRepository,
)
from shared_kernel import audit

RELEASED_OBSERVATION_TYPE = "released_observation"


@dataclass(frozen=True, slots=True)
class ReleaseResult:
    observation: AgentObservation
    message: Message | None  # set for room releases; the route emits/dispatches post-commit
    target_agent_ids: tuple[uuid.UUID, ...]  # set for private releases
    wake: bool
    content: str  # override-resolved body; the post-commit dispatcher builds the queue note from it


class ObservationService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._observations = ObservationRepository(db)
        self._rooms = ChatroomRepository(db)
        self._bindings = ChatroomAgentRepository(db)
        self._messages = MessageRepository(db)

    # ---- worker-side -------------------------------------------------------

    async def record(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        content_md: str,
        trigger: str,
        trigger_message_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> AgentObservation:
        """Persist an observer turn's output. No WS emission here — the turn
        engine emits ``observation.created`` post-commit, mirroring how agent
        replies emit ``message.created``.

        ``blocks`` ([R28.15]) is the validated presentation array, already
        serialised into ``content_md`` by the caller. Passed through rather than
        serialised here: the serialiser lives with the block schema in the agents
        runtime, and this service must not learn a block vocabulary it has no other
        reason to know."""
        return await self._observations.create(
            chatroom_id=chatroom_id,
            agent_id=agent_id,
            content_md=content_md,
            trigger=trigger,
            trigger_message_id=trigger_message_id,
            metadata=metadata,
            blocks=blocks,
        )

    # ---- creator-side ------------------------------------------------------

    async def list(
        self,
        *,
        chatroom_id: uuid.UUID,
        before: uuid.UUID | None = None,
        limit: int = 50,
    ) -> Sequence[AgentObservation]:
        return await self._observations.list(chatroom_id=chatroom_id, before=before, limit=limit)

    async def recipient_user_id(self, chatroom_id: uuid.UUID) -> uuid.UUID | None:
        """Who receives ``observation.*`` push events for this room (R28.13).

        Legacy NULL-creator rooms resolve to no push recipient — moderators
        still read observations over REST, but events are not fanned out to
        every moderator in v1.
        """
        room = await self._rooms.get(chatroom_id)
        return room.created_by_user_id if room else None

    async def release(
        self,
        *,
        chatroom_id: uuid.UUID,
        observation_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        target_room: bool,
        target_agent_ids: Sequence[uuid.UUID] = (),
        wake: bool = False,
        content_override: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> ReleaseResult:
        observation = await self._observations.get(chatroom_id=chatroom_id, observation_id=observation_id)
        if observation is None:
            raise ObservationNotFound(str(observation_id))

        targets: tuple[uuid.UUID, ...] = ()
        if not target_room:
            # R28.07: private targets must be normal-role bindings of THIS
            # room. Anything else (observer, unbound, foreign agent) is
            # rejected wholesale — no partial release, no cross-room channel.
            if not target_agent_ids:
                raise InvalidReleaseTarget("agent release requires at least one target")
            rows = await self._bindings.list(chatroom_id)
            normal = {a.agent_id for a in rows if a.role is ChatroomAgentRole.NORMAL}
            invalid = [str(a) for a in target_agent_ids if a not in normal]
            if invalid:
                raise InvalidReleaseTarget(f"not normal-role bindings of this room: {invalid}")
            targets = tuple(dict.fromkeys(target_agent_ids))

        content = content_override if content_override is not None else observation.content_md

        message: Message | None = None
        if target_room:
            room = await self._rooms.get(chatroom_id)
            disclose = bool(room and room.disclose_observers)
            meta: dict[str, Any] = {
                "type": RELEASED_OBSERVATION_TYPE,
                "observation_id": str(observation.id),
                "released_by_user_id": str(actor_user_id),
            }
            if disclose:
                # R28.09: the observer's identity travels only when the room
                # discloses observers at release time.
                meta["observer_agent_id"] = str(observation.agent_id)
            release_target: dict[str, Any] = {"kind": "room"}
        else:
            release_target = {
                "kind": "agents",
                "agent_ids": [str(a) for a in targets],
                "woken": wake,
            }

        won = await self._observations.mark_released(
            chatroom_id=chatroom_id,
            observation_id=observation_id,
            released_by_user_id=actor_user_id,
            release_target=release_target,
        )
        if not won:
            raise ObservationAlreadyReleased(str(observation_id))

        if target_room:
            # Mirrors the compaction insert (transcript store): a SYSTEM row
            # with a service-stamped metadata type. Clients cannot forge it —
            # the HTTP send endpoint rejects client metadata.
            message = await self._messages.create(
                chatroom_id=chatroom_id,
                sender_type=SenderType.SYSTEM,
                sender_id=None,
                content_md=content,
                metadata=meta,
            )
            release_target["message_id"] = str(message.id)
            await self._observations.mark_release_message(
                chatroom_id=chatroom_id,
                observation_id=observation_id,
                release_target=release_target,
            )
        # R28.08: the queue push for private targets happens post-commit in the
        # route's _dispatch_release — nothing may reach Redis for a release the
        # DB could still roll back.

        # R28.11: content never enters audit metadata.
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="observation.released",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="observation",
                resource_id=observation_id,
                metadata={
                    "chatroom_id": str(chatroom_id),
                    "target": release_target,
                    "content_overridden": content_override is not None,
                },
                request_id=request_id,
            ),
        )

        released = await self._observations.get(chatroom_id=chatroom_id, observation_id=observation_id)
        if released is None:  # pragma: no cover - row cannot vanish mid-transaction
            raise ObservationNotFound(str(observation_id))
        return ReleaseResult(
            observation=released,
            message=message,
            target_agent_ids=targets,
            wake=wake and not target_room,
            content=content,
        )

    async def delete(
        self,
        *,
        chatroom_id: uuid.UUID,
        observation_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        deleted = await self._observations.soft_delete(chatroom_id=chatroom_id, observation_id=observation_id)
        if not deleted:
            raise ObservationNotFound(str(observation_id))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="observation.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="observation",
                resource_id=observation_id,
                metadata={"chatroom_id": str(chatroom_id)},
                request_id=request_id,
            ),
        )


__all__ = ["RELEASED_OBSERVATION_TYPE", "ObservationService", "ReleaseResult"]
