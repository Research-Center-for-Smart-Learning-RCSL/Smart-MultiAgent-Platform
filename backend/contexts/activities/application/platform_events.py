"""The activity facts the platform wakes a room's teacher for ([R15.28]).

A deadline reached and every connected participant having submitted are each
claimed once per activation by a guarded UPDATE, audited here, and returned for
the caller to enqueue after its commit; this context never enqueues a turn. A
valid submission is the third, debounced by the caller into a window.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.application.activation_notice import display_name
from contexts.activities.domain.group_consent import is_individual_type
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivityType,
    ConnectedProgress,
    PlatformActivityEvent,
)
from contexts.activities.infrastructure.repositories.activation_repo import ActivationRepository
from contexts.activities.infrastructure.repositories.submission_repo import ActivitySubmissionRepository
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from shared_kernel import audit


def _quoted_name(activity_type: ActivityType | None) -> str:
    name = display_name(activity_type)
    return f'"{name}"' if name else "the activity"


def deadline_notice(activity_type: ActivityType | None) -> str:
    return f"The time limit of {_quoted_name(activity_type)} was reached."


def completion_notice(activity_type: ActivityType | None, connected: int) -> str:
    return (
        f"Every connected participant ({connected}) has a valid submission to {_quoted_name(activity_type)}."
    )


def submission_notice(activity_type: ActivityType | None, *, in_window: int, in_round: int) -> str:
    """Counts only: who submitted and what they wrote stay in the activity block."""
    plural = "" if in_window == 1 else "s"
    return (
        f"{in_window} new valid submission{plural} to {_quoted_name(activity_type)} "
        f"arrived in the last few seconds; {in_round} in this round so far."
    )


class PlatformActivityEvents:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._activations = ActivationRepository(db)
        self._types = ActivityTypeRepository(db)
        self._submissions = ActivitySubmissionRepository(db)

    async def connected_participants(self, chatroom_id: uuid.UUID) -> frozenset[uuid.UUID] | None:
        """The human participants connected now, room creator excluded.

        A participant inside the reconnect grace counts as connected, so a dropped
        socket is not dropped from the count in the seconds before it comes back.
        ``None`` when it cannot be stated truthfully: presence unavailable or
        truncated, or the room unreadable. Shared by the completion wake and the
        room group's ballot ([R30.49]), which use the same window by rule.
        """
        from contexts.conversation.interfaces.facade import HEADCOUNT_PRESENCE_LIMIT, ConversationFacade

        conversation = ConversationFacade(self._db)
        room = await conversation.get_chatroom(chatroom_id)
        if room is None:
            return None
        snapshot = await conversation.room_presence_snapshot(chatroom_id, limit=HEADCOUNT_PRESENCE_LIMIT)
        # A truncated set cannot support "everyone connected has submitted".
        if not snapshot.available or snapshot.truncated:
            return None
        reconnecting = await conversation.room_reconnecting_user_ids(chatroom_id)
        if reconnecting is None:
            return None
        return frozenset(u for u in (*snapshot.user_ids, *reconnecting) if u != room.created_by_user_id)

    async def connected_progress(
        self, chatroom_id: uuid.UUID, activation: ActivityActivation
    ) -> ConnectedProgress | None:
        """Submission progress among the participants connected now, creator excluded."""
        connected = await self.connected_participants(chatroom_id)
        if connected is None:
            return None
        submitted = await self._submissions.valid_individual_subjects(activation_id=activation.id)
        return ConnectedProgress(connected=len(connected), submitted=len(connected & submitted))

    async def claim_due_deadlines(self, *, limit: int = 100) -> list[PlatformActivityEvent]:
        events: list[PlatformActivityEvent] = []
        for activation in await self._activations.claim_due_deadlines(limit=limit):
            activity_type = await self._types.get(activation.activity_type_id)
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="activity.deadline_reached",
                    resource_type="activity_activation",
                    resource_id=activation.id,
                    metadata={
                        "chatroom_id": str(activation.chatroom_id),
                        "activity_type_id": str(activation.activity_type_id),
                        "time_limit_minutes": str(activation.time_limit_minutes),
                    },
                ),
            )
            events.append(
                PlatformActivityEvent(
                    chatroom_id=activation.chatroom_id,
                    activation_id=activation.id,
                    activity_type_id=activation.activity_type_id,
                    notice=deadline_notice(activity_type),
                )
            )
        return events

    async def claim_if_complete(
        self, *, chatroom_id: uuid.UUID, activation_id: uuid.UUID
    ) -> PlatformActivityEvent | None:
        """The all-submitted event, once, when no connected participant lacks a submission."""
        activation = await self._activations.get(activation_id)
        if (
            activation is None
            or activation.chatroom_id != chatroom_id
            or activation.status is not ActivationStatus.ACTIVE
            # Already claimed: skip the presence read and the submitter scan that
            # every later resubmission would otherwise repeat.
            or activation.completion_notified_at is not None
        ):
            return None
        activity_type = await self._types.get(activation.activity_type_id)
        if not is_individual_type(activity_type):
            return None
        progress = await self.connected_progress(chatroom_id, activation)
        if progress is None or not progress.complete:
            return None
        if not await self._activations.claim_completion(activation_id):
            return None
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="activity.all_submitted",
                resource_type="activity_activation",
                resource_id=activation_id,
                metadata={
                    "chatroom_id": str(chatroom_id),
                    "activity_type_id": str(activation.activity_type_id),
                    "connected": str(progress.connected),
                },
            ),
        )
        return PlatformActivityEvent(
            chatroom_id=chatroom_id,
            activation_id=activation_id,
            activity_type_id=activation.activity_type_id,
            notice=completion_notice(activity_type, progress.connected),
        )

    async def submission_window_event(
        self, *, chatroom_id: uuid.UUID, activation_id: uuid.UUID, window_started_at: dt.datetime
    ) -> PlatformActivityEvent | None:
        """The submission wake at the end of its window ([R15.28]), or ``None``.

        Nothing when the round has ended, when no valid submission landed in
        the window, or when one in the window produced the completion wake: the
        teacher was woken for that already. Not audited, unlike the two claimed
        facts: it is a debounce over submissions the audit trail already holds.
        """
        activation = await self._activations.get(activation_id)
        if (
            activation is None
            or activation.chatroom_id != chatroom_id
            or activation.status is not ActivationStatus.ACTIVE
        ):
            return None
        notified = activation.completion_notified_at
        if notified is not None and notified >= window_started_at:
            return None
        in_window, in_round = await self._submissions.valid_counts_since(
            activation_id=activation_id, since=window_started_at
        )
        if in_window == 0:
            return None
        activity_type = await self._types.get(activation.activity_type_id)
        return PlatformActivityEvent(
            chatroom_id=chatroom_id,
            activation_id=activation_id,
            activity_type_id=activation.activity_type_id,
            notice=submission_notice(activity_type, in_window=in_window, in_round=in_round),
        )


__all__ = [
    "PlatformActivityEvents",
    "completion_notice",
    "deadline_notice",
    "submission_notice",
]
