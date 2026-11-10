"""Enqueue the platform wakes ([R15.27], [R15.28]) once their claim has committed.

Shared by the WebSocket presence hook, the submission route and the worker
sweep, so a platform wake is the same ``wakeup_agent`` job wherever it starts.
Each fact is already claimed by the time this runs, so a failed enqueue costs
that one binding its wake and is logged; it is not retried.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.interfaces.facade import ActivitiesFacade, PlatformActivityEvent
from contexts.conversation.interfaces import RECONNECT_GRACE_SECONDS
from contexts.orchestration.interfaces.facade import OrchestrationFacade, PlatformWake
from shared_kernel.auth.clients import get_redis
from shared_kernel.db.session import async_session
from shared_kernel.queue import enqueue

_log = logging.getLogger(__name__)

TRIGGER_CLASS_START = "class_start"
TRIGGER_ACTIVITY_DEADLINE = "activity_deadline"
TRIGGER_ALL_SUBMITTED = "all_submitted"
TRIGGER_SUBMISSION_RECEIVED = "submission_received"

#: One submission wake per activation per window ([R15.28]): a class submitting
#: together wakes its teacher once, at the end, not once per student.
SUBMISSION_WAKE_WINDOW_SECONDS = 20

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


async def dispatch_all_submitted(db: AsyncSession, chatroom_id: uuid.UUID) -> bool:
    """Claim the running round's all-submitted fact after a valid submission
    committed, then wake the granted teachers ([R15.28]).

    Best-effort: the submission stands whatever happens here. Commits the
    session it is given, so call it only after the submission's own commit.
    True when this call claimed the fact, so the caller skips the submission
    wake for the same submission.
    """
    try:
        facade = ActivitiesFacade(db)
        active = await facade.get_active_activation(chatroom_id)
        if active is None:
            return False
        event = await facade.claim_all_submitted(chatroom_id=chatroom_id, activation_id=active.id)
        await db.commit()
        if event is None:
            return False
        await wake_for_activity_event(db, event, TRIGGER_ALL_SUBMITTED)
        return True
    except Exception:
        await db.rollback()
        _log.warning("all-submitted check failed for room %s", chatroom_id, exc_info=True)
        return False


async def check_all_submitted(chatroom_id: uuid.UUID) -> bool:
    """:func:`dispatch_all_submitted` in a session of its own, for a caller
    outside a request: a failure to open one is caught here too."""
    try:
        async with async_session() as db:
            return await dispatch_all_submitted(db, chatroom_id)
    except Exception:
        _log.warning("all-submitted check could not run for room %s", chatroom_id, exc_info=True)
        return False


def _submission_window_key(activation_id: uuid.UUID) -> str:
    return f"activity:submission_wake:{activation_id}"


async def dispatch_submission_received(
    db: AsyncSession, chatroom_id: uuid.UUID, submission_id: uuid.UUID
) -> None:
    """Open the round's submission-wake window for a committed valid submission
    ([R15.28]), or join the one already open.

    The first submission of a window claims it in Redis and defers the wake to
    the window's end, where :func:`run_submission_wake` counts what arrived.
    Later ones in the window find the claim and do nothing. Best-effort and
    post-commit like :func:`dispatch_all_submitted`; call it only when that
    did not claim the completion wake for this submission.
    """
    try:
        facade = ActivitiesFacade(db)
        submission = await facade.get_submission(submission_id)
        if submission is None or not submission.is_valid or submission.chatroom_id != chatroom_id:
            return
        active = await facade.get_active_activation(chatroom_id)
        if active is None:
            return
        key = _submission_window_key(active.id)
        if not await get_redis().set(key, "1", nx=True, ex=SUBMISSION_WAKE_WINDOW_SECONDS):
            return
        try:
            await enqueue(
                "submission_received_wake",
                str(chatroom_id),
                str(active.id),
                submission.created_at.isoformat(),
                _defer_by=timedelta(seconds=SUBMISSION_WAKE_WINDOW_SECONDS),
            )
        except Exception:
            # Free the window, so the next submission can open it instead of
            # the round going without a wake until it expires.
            await get_redis().delete(key)
            raise
    except Exception:
        _log.warning("submission wake could not be scheduled for room %s", chatroom_id, exc_info=True)


async def check_submission_received(chatroom_id: uuid.UUID, submission_id: uuid.UUID) -> None:
    """:func:`dispatch_submission_received` in a session of its own."""
    try:
        async with async_session() as db:
            await dispatch_submission_received(db, chatroom_id, submission_id)
    except Exception:
        _log.warning("submission wake could not run for room %s", chatroom_id, exc_info=True)


async def run_submission_wake(
    chatroom_id: uuid.UUID, activation_id: uuid.UUID, window_started_at: dt.datetime
) -> int:
    """The end of a submission window: wake the granted teachers once with the
    window's counts, unless the round ended or the completion wake covered it."""
    async with async_session() as db:
        event = await ActivitiesFacade(db).submission_window_event(
            chatroom_id=chatroom_id, activation_id=activation_id, window_started_at=window_started_at
        )
        if event is None:
            return 0
        return await wake_for_activity_event(db, event, TRIGGER_SUBMISSION_RECEIVED)


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
    "SUBMISSION_WAKE_WINDOW_SECONDS",
    "TRIGGER_ACTIVITY_DEADLINE",
    "TRIGGER_ALL_SUBMITTED",
    "TRIGGER_CLASS_START",
    "TRIGGER_SUBMISSION_RECEIVED",
    "check_all_submitted",
    "check_submission_received",
    "dispatch_all_submitted",
    "dispatch_submission_received",
    "enqueue_platform_wake",
    "run_submission_wake",
    "schedule_leave_recheck",
    "wake_for_activity_event",
]
