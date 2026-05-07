"""Chat export (R13.17, F.10).

A user submits an export job; the router enqueues an Arq task and returns
`{job_id}`. The worker writes a JSON manifest (raw markdown + sanitised
HTML, per R13.14) plus an attachments folder into the `exports` bucket
and updates the Redis-tracked job state. `GET /api/exports/{job_id}` then
resolves to a presigned URL.

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
from typing import Final

from shared_kernel.auth.clients import get_redis, now

_JOB_TTL_SECONDS: Final = 24 * 3600


def _job_key(job_id: uuid.UUID) -> str:
    return f"chat_export:{job_id}"


@dataclass(frozen=True, slots=True)
class ExportJobState:
    job_id: uuid.UUID
    chatroom_id: uuid.UUID
    owner_user_id: uuid.UUID
    status: str  # "queued" | "running" | "ready" | "failed"
    created_at: datetime
    object_key: str | None  # exports/{job_id}/manifest.json once ready
    bucket: str | None
    error: str | None = None


async def create(
    *,
    chatroom_id: uuid.UUID,
    owner_user_id: uuid.UUID,
) -> ExportJobState:
    job_id = uuid.uuid4()
    state = ExportJobState(
        job_id=job_id,
        chatroom_id=chatroom_id,
        owner_user_id=owner_user_id,
        status="queued",
        created_at=now(),
        object_key=None,
        bucket=None,
    )
    await _store(state)
    return state


async def mark_running(job_id: uuid.UUID) -> None:
    state = await get(job_id)
    if state is None:
        return
    await _store(_replace(state, status="running"))


async def mark_ready(
    *,
    job_id: uuid.UUID,
    bucket: str,
    object_key: str,
) -> None:
    state = await get(job_id)
    if state is None:
        return
    await _store(_replace(state, status="ready", bucket=bucket, object_key=object_key))


async def mark_failed(*, job_id: uuid.UUID, error: str) -> None:
    state = await get(job_id)
    if state is None:
        return
    await _store(_replace(state, status="failed", error=error))


async def get(job_id: uuid.UUID) -> ExportJobState | None:
    raw = await get_redis().get(_job_key(job_id))
    if raw is None:
        return None
    data = json.loads(raw)
    return ExportJobState(
        job_id=uuid.UUID(data["job_id"]),
        chatroom_id=uuid.UUID(data["chatroom_id"]),
        owner_user_id=uuid.UUID(data["owner_user_id"]),
        status=data["status"],
        created_at=datetime.fromisoformat(data["created_at"]),
        object_key=data.get("object_key"),
        bucket=data.get("bucket"),
        error=data.get("error"),
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
        "error": state.error,
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
        error=kwargs.get("error", state.error),  # type: ignore[arg-type]
    )


_ = timedelta  # imported for future-TTL tuning; keep in surface


__all__ = [
    "ExportJobState",
    "create",
    "get",
    "mark_failed",
    "mark_ready",
    "mark_running",
]
