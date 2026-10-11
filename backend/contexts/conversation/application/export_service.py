"""Chat export (R13.17, F.10).

A user submits an export job; the router enqueues an Arq task and returns
`{job_id}`. The worker writes the transcript (and, when asked, a ZIP with an
`attachments/` folder) into the `exports` bucket and updates the
Redis-tracked job state. `GET /api/exports/{job_id}` then mints a download
link. A user may have one export in progress per room (`create`).

Job state is kept in Redis (not Postgres) because:
  - the payload is ephemeral (24h bucket lifecycle),
  - the Redis TTL matches the lifecycle exactly, so orphan cleanup is
    free,
  - state contention is zero — only the single worker writes; the web
    layer only reads.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from contexts.conversation.domain.errors import ExportInProgress
from contexts.conversation.domain.models import ExportSenderScope
from shared_kernel.auth.clients import get_redis, now

_JOB_TTL_SECONDS: Final = 24 * 3600
# How long the worker lets one export run (its arq job timeout): long enough
# to copy the 2 GiB attachment budget into an archive and upload it.
CHAT_EXPORT_TIMEOUT_S: Final = 30 * 60
# A job older than this can no longer be queued or running in earnest: it is
# the run limit plus as long again for queue wait. Past it, a job still reading
# queued or running was abandoned (worker killed mid-job), and must not hold the room.
ACTIVE_EXPORT_STALE_AFTER: Final = timedelta(seconds=2 * CHAT_EXPORT_TIMEOUT_S)


class ExportJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


def _job_key(job_id: uuid.UUID) -> str:
    return f"chat_export:{job_id}"


def _active_key(owner_user_id: uuid.UUID, chatroom_id: uuid.UUID) -> str:
    return f"chat_export:active:{owner_user_id}:{chatroom_id}"


@dataclass(frozen=True, slots=True)
class ExportJobState:
    job_id: uuid.UUID
    chatroom_id: uuid.UUID
    owner_user_id: uuid.UUID
    status: ExportJobStatus
    created_at: datetime
    object_key: str | None  # exports/{job_id}/<filename> once ready
    bucket: str | None
    # Export shape, chosen at request time. The date window is resolved to
    # concrete bounds by the API from the preset (last_7_days, custom, …) so
    # the worker stays deterministic regardless of when it runs.
    export_format: str = "markdown"  # "markdown" | "json" | "pdf"
    created_after: datetime | None = None
    created_before: datetime | None = None
    # Permission-matrix row 19 scope the API authorized. Recorded so the worker
    # can detect a widening, not so it can skip re-deriving: the worker treats
    # this as a description and the database as the authority.
    sender_scope: ExportSenderScope = ExportSenderScope.OWN_PLUS_NON_USER
    error: str | None = None
    # Captured at request time for the download filename; the status endpoint
    # has no database session to look it up later.
    room_name: str | None = None
    # A ZIP with the attachment files; a record from before the option existed
    # reads False, the single-transcript export it was.
    include_attachments: bool = False

    @property
    def is_active(self) -> bool:
        return self.status in (ExportJobStatus.QUEUED, ExportJobStatus.RUNNING)


async def create(
    *,
    chatroom_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    export_format: str = "markdown",
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    sender_scope: ExportSenderScope = ExportSenderScope.OWN_PLUS_NON_USER,
    room_name: str | None = None,
    include_attachments: bool = False,
) -> ExportJobState:
    """Record a queued job, claiming the owner's one active export slot for the room.

    Raises ``ExportInProgress`` (carrying the running job's id) while the owner
    already has a queued or running export of this room.
    """
    job_id = uuid.uuid4()
    state = ExportJobState(
        job_id=job_id,
        chatroom_id=chatroom_id,
        owner_user_id=owner_user_id,
        status=ExportJobStatus.QUEUED,
        created_at=now(),
        object_key=None,
        bucket=None,
        export_format=export_format,
        created_after=created_after,
        created_before=created_before,
        sender_scope=sender_scope,
        room_name=room_name,
        include_attachments=include_attachments,
    )
    # Stored before the slot is claimed, so a concurrent create that finds the
    # slot taken can always read the holder's record and see it is active.
    await _store(state)
    try:
        await _claim_active(owner_user_id, chatroom_id, job_id)
    except ExportInProgress:
        await get_redis().delete(_job_key(job_id))
        raise
    return state


# Replace the slot's holder only if it is still the one that was inspected, so
# two creates taking over a finished holder at once cannot both win.
_TAKEOVER: Final = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
  return 1
end
return 0
"""


async def _claim_active(owner_user_id: uuid.UUID, chatroom_id: uuid.UUID, job_id: uuid.UUID) -> None:
    redis = get_redis()
    key = _active_key(owner_user_id, chatroom_id)
    for _ in range(3):
        if await redis.set(key, str(job_id), ex=_JOB_TTL_SECONDS, nx=True):
            return
        held = await redis.get(key)
        if held is None:
            continue  # released between the two calls; claim it afresh
        holder = await get(uuid.UUID(held))
        if holder is not None and holder.is_active:
            if now() - holder.created_at < ACTIVE_EXPORT_STALE_AFTER:
                raise ExportInProgress(job_id=holder.job_id)
            # Abandoned (worker killed mid-job): it must not read running forever.
            await _store(_replace(holder, status=ExportJobStatus.FAILED, error="abandoned"))
        # The holder finished, expired or was abandoned: take the slot over.
        if await redis.eval(_TAKEOVER, 1, key, held, str(job_id), str(_JOB_TTL_SECONDS)):
            return
    # Lost every race for the slot: another create for this room just won it.
    winner = await redis.get(key)
    raise ExportInProgress(job_id=uuid.UUID(winner) if winner else job_id)


async def _release_active(state: ExportJobState) -> None:
    redis = get_redis()
    key = _active_key(state.owner_user_id, state.chatroom_id)
    # Only the job holding the slot releases it, so a stale job finishing late
    # cannot free the slot of the export that replaced it.
    if await redis.get(key) == str(state.job_id):
        await redis.delete(key)


async def mark_running(job_id: uuid.UUID) -> None:
    state = await get(job_id)
    if state is None:
        return
    await _store(_replace(state, status=ExportJobStatus.RUNNING))


async def mark_ready(
    *,
    job_id: uuid.UUID,
    bucket: str,
    object_key: str,
) -> None:
    state = await get(job_id)
    if state is None:
        return
    await _store(_replace(state, status=ExportJobStatus.READY, bucket=bucket, object_key=object_key))
    await _release_active(state)


async def mark_failed(*, job_id: uuid.UUID, error: str) -> None:
    state = await get(job_id)
    if state is None:
        return
    await _store(_replace(state, status=ExportJobStatus.FAILED, error=error))
    await _release_active(state)


async def get(job_id: uuid.UUID) -> ExportJobState | None:
    raw = await get_redis().get(_job_key(job_id))
    if raw is None:
        return None
    data = json.loads(raw)
    after = data.get("created_after")
    before = data.get("created_before")
    return ExportJobState(
        job_id=uuid.UUID(data["job_id"]),
        chatroom_id=uuid.UUID(data["chatroom_id"]),
        owner_user_id=uuid.UUID(data["owner_user_id"]),
        status=ExportJobStatus(data["status"]),
        created_at=datetime.fromisoformat(data["created_at"]),
        object_key=data.get("object_key"),
        bucket=data.get("bucket"),
        export_format=data.get("export_format", "markdown"),
        created_after=datetime.fromisoformat(after) if after else None,
        created_before=datetime.fromisoformat(before) if before else None,
        # A record written before sender scoping existed has no key. Default to
        # the *narrowest* scope so a pre-deploy job can never widen an export;
        # such a job either fails closed on the widen check or produces a
        # narrowed archive, both of which are safe outcomes.
        sender_scope=ExportSenderScope(data.get("sender_scope", ExportSenderScope.OWN_PLUS_NON_USER.value)),
        error=data.get("error"),
        room_name=data.get("room_name"),
        include_attachments=bool(data.get("include_attachments", False)),
    )


async def _store(state: ExportJobState) -> None:
    payload = {
        "job_id": str(state.job_id),
        "chatroom_id": str(state.chatroom_id),
        "owner_user_id": str(state.owner_user_id),
        "status": state.status,
        "created_at": state.created_at.isoformat(),
        "object_key": state.object_key,
        "bucket": state.bucket,
        "export_format": state.export_format,
        "created_after": state.created_after.isoformat() if state.created_after else None,
        "created_before": state.created_before.isoformat() if state.created_before else None,
        "sender_scope": state.sender_scope.value,
        "error": state.error,
        "room_name": state.room_name,
        "include_attachments": state.include_attachments,
    }
    await get_redis().set(
        _job_key(state.job_id),
        json.dumps(payload),
        ex=_JOB_TTL_SECONDS,
    )


def _replace(state: ExportJobState, **kwargs: object) -> ExportJobState:
    return ExportJobState(
        job_id=state.job_id,
        chatroom_id=state.chatroom_id,
        owner_user_id=state.owner_user_id,
        status=kwargs.get("status", state.status),  # type: ignore[arg-type]
        created_at=state.created_at,
        object_key=kwargs.get("object_key", state.object_key),  # type: ignore[arg-type]
        bucket=kwargs.get("bucket", state.bucket),  # type: ignore[arg-type]
        export_format=state.export_format,
        created_after=state.created_after,
        created_before=state.created_before,
        sender_scope=state.sender_scope,
        error=kwargs.get("error", state.error),  # type: ignore[arg-type]
        room_name=state.room_name,
        include_attachments=state.include_attachments,
    )


__all__ = [
    "ACTIVE_EXPORT_STALE_AFTER",
    "CHAT_EXPORT_TIMEOUT_S",
    "ExportJobState",
    "ExportJobStatus",
    "create",
    "get",
    "mark_failed",
    "mark_ready",
    "mark_running",
]
