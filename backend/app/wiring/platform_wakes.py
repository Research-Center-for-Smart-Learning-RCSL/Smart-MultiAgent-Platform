"""Enqueue the platform wakes ([R15.27], [R15.28]) once their claim has committed.

Shared by the WebSocket presence hook, the submission route and the worker
sweep, so a platform wake is the same ``wakeup_agent`` job wherever it starts.
Each fact is already claimed by the time this runs, so a failed enqueue costs
that one binding its wake and is logged; it is not retried.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.interfaces.facade import ActivitiesFacade, PlatformActivityEvent
from contexts.conversation.interfaces import RECONNECT_GRACE_SECONDS
from contexts.orchestration.interfaces.facade import OrchestrationFacade, PlatformWake
from shared_kernel.db.session import async_session
from shared_kernel.queue import enqueue

_log = logging.getLogger(__name__)

TRIGGER_CLASS_START = "class_start"
TRIGGER_ACTIVITY_DEADLINE = "activity_deadline"
TRIGGER_ALL_SUBMITTED = "all_submitted"

# The grace runs on Redis's clock and the deferral on the worker's; the margin keeps
# a job that runs a little early from reading the leaver as still reconnecting, which
# would leave the round to its deadline.
LEAVE_RECHECK_DELAY_SECONDS = RECONNECT_GRACE_SECONDS + 5


async def enqueue_platform_wake(room_id: uuid.UUID, trigger: str, wake: PlatformWake) -> int:
    queued = 0
    for target in wake.targets:
        try:
            await enqueue(
                "wakeup_agent",
                str(target.agent_id),
                str(room_id),
                trigger,
                None,
                str(target.binding_id),
                wake.notice,
            )
            queued += 1
        except Exception:
            _log.warning(
                "%s enqueue failed for agent %s in room %s", trigger, target.agent_id, room_id, exc_info=True
            )
    return queued


async def wake_for_activity_event(db: AsyncSession, event: PlatformActivityEvent, trigger: str) -> int:
    """Wake the bindings whose grant covers the event's activity type. Post-commit only."""
    targets = await OrchestrationFacade(db).granted_targets(
        room_id=event.chatroom_id, activity_type_id=event.activity_type_id
    )
    return await enqueue_platform_wake(event.chatroom_id, trigger, PlatformWake(targets, event.notice))


async def dispatch_all_submitted(db: AsyncSession, chatroom_id: uuid.UUID) -> None:
    """Claim the running round's all-submitted fact after a valid submission
    committed, then wake the granted teachers ([R15.28]).

    Best-effort: the submission stands whatever happens here. Commits the
    session it is given, so call it only after the submission's own commit.
    """
    try:
        facade = ActivitiesFacade(db)
        active = await facade.get_active_activation(chatroom_id)
        if active is None:
            return
        event = await facade.claim_all_submitted(chatroom_id=chatroom_id, activation_id=active.id)
        await db.commit()
        if event is not None:
            await wake_for_activity_event(db, event, TRIGGER_ALL_SUBMITTED)
    except Exception:
        await db.rollback()
        _log.warning("all-submitted check failed for room %s", chatroom_id, exc_info=True)


async def check_all_submitted(chatroom_id: uuid.UUID) -> None:
    """:func:`dispatch_all_submitted` in a session of its own, for a caller
    outside a request: a failure to open one is caught here too."""
    try:
        async with async_session() as db:
            await dispatch_all_submitted(db, chatroom_id)
    except Exception:
        _log.warning("all-submitted check could not run for room %s", chatroom_id, exc_info=True)


async def schedule_leave_recheck(chatroom_id: uuid.UUID) -> None:
    """[R15.28]: a participant's last connection closed. Checking now would find them
    reconnecting, so the check runs once the grace has run out. A lost enqueue
    leaves that round to its deadline or the next submission."""
    try:
        await enqueue(
            "check_all_submitted",
            str(chatroom_id),
            _defer_by=timedelta(seconds=LEAVE_RECHECK_DELAY_SECONDS),
        )
    except Exception:
        _log.warning("all-submitted recheck enqueue failed for room %s", chatroom_id, exc_info=True)


__all__ = [
    "LEAVE_RECHECK_DELAY_SECONDS",
    "TRIGGER_ACTIVITY_DEADLINE",
    "TRIGGER_ALL_SUBMITTED",
    "TRIGGER_CLASS_START",
    "check_all_submitted",
    "dispatch_all_submitted",
    "enqueue_platform_wake",
    "schedule_leave_recheck",
    "wake_for_activity_event",
]
