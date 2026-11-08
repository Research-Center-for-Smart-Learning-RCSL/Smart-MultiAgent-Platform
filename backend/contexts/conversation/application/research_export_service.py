"""Research data export job state (R33.10).

Workspace-scoped de-identified export of submissions, observations and
transcripts. Job state is kept in Redis (not Postgres), mirroring the chat
export in ``export_service.py`` with a separate key namespace. One key per
owner and workspace names their latest job: it blocks a second export while
that job runs, and lets the dashboard re-open the job after the dialog that
started it is gone (2026-11-08-research-data-integrity, Q-3).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from contexts.conversation.application import export_slot
from contexts.conversation.domain.errors import ResearchExportInProgress
from shared_kernel.auth.clients import get_redis, now

# Until a job is ready, its record lives as long as an archive could.
_JOB_TTL_SECONDS: Final = 24 * 3600
# A ready record must expire before its archive can be purged: the retention
# sweep and the exports bucket lifecycle both delete objects 24 h after upload,
# so the record ends an hour short of that, counted from the upload's start.
READY_LIFETIME: Final = timedelta(hours=23)
RESEARCH_EXPORT_TIMEOUT_S: Final = 15 * 60
# Past this a job still reading queued or running was abandoned (worker killed
# mid-job) and must not keep the workspace's slot. Counted from the start of the
# run, or from creation while queued: a job that waited in the queue may still
# run its full timeout after it starts.
ACTIVE_EXPORT_STALE_AFTER: Final = timedelta(seconds=2 * RESEARCH_EXPORT_TIMEOUT_S)


class ResearchExportJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


def _job_key(job_id: uuid.UUID) -> str:
    return f"research_export:{job_id}"


def _latest_key(owner_user_id: uuid.UUID, workspace_id: uuid.UUID) -> str:
    return f"research_export:latest:{owner_user_id}:{workspace_id}"


@dataclass(frozen=True, slots=True)
class ResearchExportJobState:
    job_id: uuid.UUID
    workspace_id: uuid.UUID
    owner_user_id: uuid.UUID
    status: ResearchExportJobStatus
    created_at: datetime
    object_key: str | None = None
    bucket: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    actor_ip: str | None = None
    error: str | None = None
    upload_started_at: datetime | None = None
    running_since: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status in (ResearchExportJobStatus.QUEUED, ResearchExportJobStatus.RUNNING)


async def create(
    *,
    workspace_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    actor_ip: str | None = None,
) -> ResearchExportJobState:
    """Record a queued job and make it the owner's latest for the workspace.

    Raises ``ResearchExportInProgress`` (carrying the running job's id) while
    the owner already has a queued or running export of this workspace.
    """
    state = ResearchExportJobState(
        job_id=uuid.uuid4(),
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        status=ResearchExportJobStatus.QUEUED,
        created_at=now(),
        created_after=created_after,
        created_before=created_before,
        actor_ip=actor_ip,
    )
    # Stored before the slot is claimed, so a concurrent create that finds the
    # slot taken can always read the holder's record and see it is active.
    await _store(state)
    holder = await export_slot.claim(
        get_redis(),
        _latest_key(owner_user_id, workspace_id),
        state.job_id,
        ttl_seconds=_JOB_TTL_SECONDS,
        blocks=_blocks,
    )
    if holder is not None:
        await get_redis().delete(_job_key(state.job_id))
        raise ResearchExportInProgress(job_id=holder)
    return state


async def _blocks(holder_id: uuid.UUID) -> bool:
    holder = await get(holder_id)
    if holder is None or not holder.is_active:
        return False
    if now() - (holder.running_since or holder.created_at) < ACTIVE_EXPORT_STALE_AFTER:
        return True
    await _store(replace(holder, status=ResearchExportJobStatus.FAILED, error="abandoned"))
    return False


async def latest(*, owner_user_id: uuid.UUID, workspace_id: uuid.UUID) -> ResearchExportJobState | None:
    held = await get_redis().get(_latest_key(owner_user_id, workspace_id))
    return None if held is None else await get(uuid.UUID(held))


async def mark_running(job_id: uuid.UUID) -> bool:
    """Start the job; ``False`` when it is not queued, so it must not run."""
    state = await get(job_id)
    if state is None or state.status is not ResearchExportJobStatus.QUEUED:
        return False
    await _store(replace(state, status=ResearchExportJobStatus.RUNNING, running_since=now()))
    return True


async def mark_ready(
    *,
    job_id: uuid.UUID,
    bucket: str,
    object_key: str,
    upload_started_at: datetime,
) -> bool:
    """``upload_started_at`` is taken before the upload, so the archive is no older than it.

    ``False`` when the job is no longer running: a newer export took the slot
    over and marked it abandoned, and the failure stands.
    """
    state = await get(job_id)
    if state is None or state.status is not ResearchExportJobStatus.RUNNING:
        return False
    ready = replace(
        state,
        status=ResearchExportJobStatus.READY,
        bucket=bucket,
        object_key=object_key,
        upload_started_at=upload_started_at,
    )
    remaining = int((upload_started_at + READY_LIFETIME - now()).total_seconds())
    if remaining <= 0:
        # The build outlived the archive's own lifetime; nothing is left to offer.
        await _store(replace(ready, status=ResearchExportJobStatus.FAILED, error="expired before ready"))
        return False
    await _store(ready, ttl_seconds=remaining)
    return True


async def mark_failed(*, job_id: uuid.UUID, error: str) -> None:
    """A finished job keeps its outcome."""
    state = await get(job_id)
    if state is None or not state.is_active:
        return
    await _store(replace(state, status=ResearchExportJobStatus.FAILED, error=error))


async def get(job_id: uuid.UUID) -> ResearchExportJobState | None:
    raw = await get_redis().get(_job_key(job_id))
    if raw is None:
        return None
    data = json.loads(raw)
    return ResearchExportJobState(
        job_id=uuid.UUID(data["job_id"]),
        workspace_id=uuid.UUID(data["workspace_id"]),
        owner_user_id=uuid.UUID(data["owner_user_id"]),
        status=ResearchExportJobStatus(data["status"]),
        created_at=datetime.fromisoformat(data["created_at"]),
        object_key=data.get("object_key"),
        bucket=data.get("bucket"),
        created_after=_dt(data.get("created_after")),
        created_before=_dt(data.get("created_before")),
        actor_ip=data.get("actor_ip"),
        error=data.get("error"),
        upload_started_at=_dt(data.get("upload_started_at")),
        running_since=_dt(data.get("running_since")),
    )


def _dt(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def _store(state: ResearchExportJobState, *, ttl_seconds: int = _JOB_TTL_SECONDS) -> None:
    payload = {
        "job_id": str(state.job_id),
        "workspace_id": str(state.workspace_id),
        "owner_user_id": str(state.owner_user_id),
        "status": state.status.value,
        "created_at": state.created_at.isoformat(),
        "object_key": state.object_key,
        "bucket": state.bucket,
        "created_after": _iso(state.created_after),
        "created_before": _iso(state.created_before),
        "actor_ip": state.actor_ip,
        "error": state.error,
        "upload_started_at": _iso(state.upload_started_at),
        "running_since": _iso(state.running_since),
    }
    await get_redis().set(_job_key(state.job_id), json.dumps(payload), ex=ttl_seconds)


__all__ = [
    "ACTIVE_EXPORT_STALE_AFTER",
    "READY_LIFETIME",
    "RESEARCH_EXPORT_TIMEOUT_S",
    "ResearchExportJobState",
    "ResearchExportJobStatus",
    "create",
    "get",
    "latest",
    "mark_failed",
    "mark_ready",
    "mark_running",
]
