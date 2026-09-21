"""Research data export job state (R33.10).

Workspace-scoped de-identified export of submissions, observations and
transcripts. Job state is kept in Redis (not Postgres) with a 72h TTL,
matching the presigned URL expiry. Mirrors the chat export pattern in
``export_service.py`` with a separate key namespace.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from shared_kernel.auth.clients import get_redis, now

_JOB_TTL_SECONDS: Final = 72 * 3600


class ResearchExportJobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


def _job_key(job_id: uuid.UUID) -> str:
    return f"research_export:{job_id}"


@dataclass(frozen=True, slots=True)
class ResearchExportJobState:
    job_id: uuid.UUID
    workspace_id: uuid.UUID
    owner_user_id: uuid.UUID
    status: str
    created_at: datetime
    object_key: str | None = None
    bucket: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    actor_ip: str | None = None
    error: str | None = None


async def create(
    *,
    workspace_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    actor_ip: str | None = None,
) -> ResearchExportJobState:
    job_id = uuid.uuid4()
    state = ResearchExportJobState(
        job_id=job_id,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        status=ResearchExportJobStatus.QUEUED,
        created_at=now(),
        created_after=created_after,
        created_before=created_before,
        actor_ip=actor_ip,
    )
    await _store(state)
    return state


async def mark_running(job_id: uuid.UUID) -> None:
    state = await get(job_id)
    if state is None:
        return
    await _store(_replace(state, status=ResearchExportJobStatus.RUNNING))


async def mark_ready(
    *,
    job_id: uuid.UUID,
    bucket: str,
    object_key: str,
) -> None:
    state = await get(job_id)
    if state is None:
        return
    await _store(_replace(state, status=ResearchExportJobStatus.READY, bucket=bucket, object_key=object_key))


async def mark_failed(*, job_id: uuid.UUID, error: str) -> None:
    state = await get(job_id)
    if state is None:
        return
    await _store(_replace(state, status=ResearchExportJobStatus.FAILED, error=error))


async def get(job_id: uuid.UUID) -> ResearchExportJobState | None:
    raw = await get_redis().get(_job_key(job_id))
    if raw is None:
        return None
    data = json.loads(raw)
    after = data.get("created_after")
    before = data.get("created_before")
    return ResearchExportJobState(
        job_id=uuid.UUID(data["job_id"]),
        workspace_id=uuid.UUID(data["workspace_id"]),
        owner_user_id=uuid.UUID(data["owner_user_id"]),
        status=data["status"],
        created_at=datetime.fromisoformat(data["created_at"]),
        object_key=data.get("object_key"),
        bucket=data.get("bucket"),
        created_after=datetime.fromisoformat(after) if after else None,
        created_before=datetime.fromisoformat(before) if before else None,
        actor_ip=data.get("actor_ip"),
        error=data.get("error"),
    )


async def _store(state: ResearchExportJobState) -> None:
    payload = {
        "job_id": str(state.job_id),
        "workspace_id": str(state.workspace_id),
        "owner_user_id": str(state.owner_user_id),
        "status": state.status,
        "created_at": state.created_at.isoformat(),
        "object_key": state.object_key,
        "bucket": state.bucket,
        "created_after": state.created_after.isoformat() if state.created_after else None,
        "created_before": state.created_before.isoformat() if state.created_before else None,
        "actor_ip": state.actor_ip,
        "error": state.error,
    }
    await get_redis().set(
        _job_key(state.job_id),
        json.dumps(payload),
        ex=_JOB_TTL_SECONDS,
    )


def _replace(state: ResearchExportJobState, **kwargs: object) -> ResearchExportJobState:
    return ResearchExportJobState(
        job_id=state.job_id,
        workspace_id=state.workspace_id,
        owner_user_id=state.owner_user_id,
        status=kwargs.get("status", state.status),  # type: ignore[arg-type]
        created_at=state.created_at,
        object_key=kwargs.get("object_key", state.object_key),  # type: ignore[arg-type]
        bucket=kwargs.get("bucket", state.bucket),  # type: ignore[arg-type]
        created_after=state.created_after,
        created_before=state.created_before,
        actor_ip=state.actor_ip,
        error=kwargs.get("error", state.error),  # type: ignore[arg-type]
    )


__all__ = [
    "ResearchExportJobState",
    "ResearchExportJobStatus",
    "create",
    "get",
    "mark_failed",
    "mark_ready",
    "mark_running",
]
