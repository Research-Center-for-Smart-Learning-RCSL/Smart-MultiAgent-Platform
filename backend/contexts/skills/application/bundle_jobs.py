"""Ephemeral job state for bundle import/export transport (§31, Phase 4 / D-58).

Import and export are `202 + task id` background jobs (§6): the web layer stages the
work and enqueues an Arq task, and the client polls a status endpoint. This module holds
that job state, mirroring `conversation.application.export_service` deliberately —
**Redis, not Postgres** — for the reasons that module gives: the payload is ephemeral (the
zip lands in a lifecycle-bounded bucket), a TTL matches that lifecycle so orphan cleanup is
free, and contention is nil (only the single worker writes; the web layer only reads).

The per-org concurrent-import limit (§6/§7/§8) lives here too: import is the one path a
tenant can drive a worker and MinIO with, so a bounded number of in-flight imports per
owning org is the backpressure. The slot counter is a plain `INCR`/`DECR` with a safety
TTL, so a worker that dies without releasing leaks at most one slot for `_SLOT_TTL_SECONDS`
rather than wedging the org forever.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from shared_kernel.auth.clients import get_redis, now

# 24 h, matching the `skill-bundles` / `exports` bucket lifecycle the payloads live under —
# the same value `export_service` pins for the same reason.
_JOB_TTL_SECONDS: Final = 24 * 3600

# Longer than any plausible single import (a 128 MB inflate + a per-file ClamAV pass over
# up to 500 files), so the slot a crashed worker fails to release is reclaimed by expiry
# rather than held indefinitely. Short enough that such a leak is self-healing well inside
# an operator's attention span.
_SLOT_TTL_SECONDS: Final = 3600

# The ceiling §6/§7/§8 name without a number. Three concurrent imports per owning org is
# enough that no legitimate user waits while bounding a tenant's claim on the shared worker
# and MinIO. A module constant rather than a settings knob because nothing has asked for it
# to be tunable, and a second unexercised setting is its own cost.
MAX_CONCURRENT_IMPORTS_PER_ORG: Final = 3


class BundleJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


def _import_key(job_id: uuid.UUID) -> str:
    return f"skill_import:{job_id}"


def _export_key(job_id: uuid.UUID) -> str:
    return f"skill_export:{job_id}"


def _slot_key(org_key: str) -> str:
    return f"skill_import:slots:{org_key}"


# ---------------------------------------------------------------------------
# import jobs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImportJobState:
    job_id: uuid.UUID
    scope: str
    owner_id: uuid.UUID | None
    # The initiator. The status endpoint authorises against it (initiator or admin), the
    # same shape `get_export` uses — the job id is an unguessable capability and this is the
    # second fence around it.
    actor_user_id: uuid.UUID
    status: BundleJobStatus
    created_at: datetime
    # Populated only once READY: the skill the bundle produced, and any compatibility
    # warnings (a network-using script, a tolerated unknown frontmatter key).
    skill_id: uuid.UUID | None = None
    warnings: tuple[str, ...] = ()
    # Populated only once FAILED: a client-safe reason (the RFC 7807 title of the
    # BundleInvalid/BundleQuarantined that rejected it), never a stack trace.
    error: str | None = None


async def create_import(
    *,
    scope: str,
    owner_id: uuid.UUID | None,
    actor_user_id: uuid.UUID,
) -> ImportJobState:
    state = ImportJobState(
        job_id=uuid.uuid4(),
        scope=scope,
        owner_id=owner_id,
        actor_user_id=actor_user_id,
        status=BundleJobStatus.QUEUED,
        created_at=now(),
    )
    await _store_import(state)
    return state


async def get_import(job_id: uuid.UUID) -> ImportJobState | None:
    raw = await get_redis().get(_import_key(job_id))
    if raw is None:
        return None
    d = json.loads(raw)
    owner = d.get("owner_id")
    skill = d.get("skill_id")
    return ImportJobState(
        job_id=uuid.UUID(d["job_id"]),
        scope=d["scope"],
        owner_id=uuid.UUID(owner) if owner else None,
        actor_user_id=uuid.UUID(d["actor_user_id"]),
        status=BundleJobStatus(d["status"]),
        created_at=datetime.fromisoformat(d["created_at"]),
        skill_id=uuid.UUID(skill) if skill else None,
        warnings=tuple(d.get("warnings", ())),
        error=d.get("error"),
    )


async def mark_import_running(job_id: uuid.UUID) -> None:
    state = await get_import(job_id)
    if state is not None:
        await _store_import(_replace_import(state, status=BundleJobStatus.RUNNING))


async def mark_import_ready(*, job_id: uuid.UUID, skill_id: uuid.UUID, warnings: tuple[str, ...]) -> None:
    state = await get_import(job_id)
    if state is not None:
        await _store_import(
            _replace_import(state, status=BundleJobStatus.READY, skill_id=skill_id, warnings=warnings)
        )


async def mark_import_failed(*, job_id: uuid.UUID, error: str) -> None:
    state = await get_import(job_id)
    if state is not None:
        await _store_import(_replace_import(state, status=BundleJobStatus.FAILED, error=error))


async def _store_import(state: ImportJobState) -> None:
    payload = {
        "job_id": str(state.job_id),
        "scope": state.scope,
        "owner_id": str(state.owner_id) if state.owner_id else None,
        "actor_user_id": str(state.actor_user_id),
        "status": state.status,
        "created_at": state.created_at.isoformat(),
        "skill_id": str(state.skill_id) if state.skill_id else None,
        "warnings": list(state.warnings),
        "error": state.error,
    }
    await get_redis().set(_import_key(state.job_id), json.dumps(payload), ex=_JOB_TTL_SECONDS)


def _replace_import(
    state: ImportJobState,
    *,
    status: BundleJobStatus,
    skill_id: uuid.UUID | None = None,
    warnings: tuple[str, ...] | None = None,
    error: str | None = None,
) -> ImportJobState:
    return ImportJobState(
        job_id=state.job_id,
        scope=state.scope,
        owner_id=state.owner_id,
        actor_user_id=state.actor_user_id,
        status=status,
        created_at=state.created_at,
        skill_id=skill_id if skill_id is not None else state.skill_id,
        warnings=warnings if warnings is not None else state.warnings,
        error=error if error is not None else state.error,
    )


# ---------------------------------------------------------------------------
# export jobs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExportJobState:
    job_id: uuid.UUID
    skill_id: uuid.UUID
    scope: str
    owner_id: uuid.UUID | None
    actor_user_id: uuid.UUID
    status: BundleJobStatus
    created_at: datetime
    # Populated only once READY: where the finished .zip landed, fetched by presigned URL.
    bucket: str | None = None
    object_key: str | None = None
    error: str | None = None


async def create_export(
    *,
    skill_id: uuid.UUID,
    scope: str,
    owner_id: uuid.UUID | None,
    actor_user_id: uuid.UUID,
) -> ExportJobState:
    state = ExportJobState(
        job_id=uuid.uuid4(),
        skill_id=skill_id,
        scope=scope,
        owner_id=owner_id,
        actor_user_id=actor_user_id,
        status=BundleJobStatus.QUEUED,
        created_at=now(),
    )
    await _store_export(state)
    return state


async def get_export(job_id: uuid.UUID) -> ExportJobState | None:
    raw = await get_redis().get(_export_key(job_id))
    if raw is None:
        return None
    d = json.loads(raw)
    owner = d.get("owner_id")
    return ExportJobState(
        job_id=uuid.UUID(d["job_id"]),
        skill_id=uuid.UUID(d["skill_id"]),
        scope=d["scope"],
        owner_id=uuid.UUID(owner) if owner else None,
        actor_user_id=uuid.UUID(d["actor_user_id"]),
        status=BundleJobStatus(d["status"]),
        created_at=datetime.fromisoformat(d["created_at"]),
        bucket=d.get("bucket"),
        object_key=d.get("object_key"),
        error=d.get("error"),
    )


async def mark_export_running(job_id: uuid.UUID) -> None:
    state = await get_export(job_id)
    if state is not None:
        await _store_export(_replace_export(state, status=BundleJobStatus.RUNNING))


async def mark_export_ready(*, job_id: uuid.UUID, bucket: str, object_key: str) -> None:
    state = await get_export(job_id)
    if state is not None:
        await _store_export(
            _replace_export(state, status=BundleJobStatus.READY, bucket=bucket, object_key=object_key)
        )


async def mark_export_failed(*, job_id: uuid.UUID, error: str) -> None:
    state = await get_export(job_id)
    if state is not None:
        await _store_export(_replace_export(state, status=BundleJobStatus.FAILED, error=error))


async def _store_export(state: ExportJobState) -> None:
    payload = {
        "job_id": str(state.job_id),
        "skill_id": str(state.skill_id),
        "scope": state.scope,
        "owner_id": str(state.owner_id) if state.owner_id else None,
        "actor_user_id": str(state.actor_user_id),
        "status": state.status,
        "created_at": state.created_at.isoformat(),
        "bucket": state.bucket,
        "object_key": state.object_key,
        "error": state.error,
    }
    await get_redis().set(_export_key(state.job_id), json.dumps(payload), ex=_JOB_TTL_SECONDS)


def _replace_export(
    state: ExportJobState,
    *,
    status: BundleJobStatus,
    bucket: str | None = None,
    object_key: str | None = None,
    error: str | None = None,
) -> ExportJobState:
    return ExportJobState(
        job_id=state.job_id,
        skill_id=state.skill_id,
        scope=state.scope,
        owner_id=state.owner_id,
        actor_user_id=state.actor_user_id,
        status=status,
        created_at=state.created_at,
        bucket=bucket if bucket is not None else state.bucket,
        object_key=object_key if object_key is not None else state.object_key,
        error=error if error is not None else state.error,
    )


# ---------------------------------------------------------------------------
# per-org concurrency slots
# ---------------------------------------------------------------------------


async def acquire_import_slot(org_key: str) -> bool:
    """Claim one of the org's import slots, or refuse. `INCR`-then-check-then-`DECR`.

    The increment is atomic, so two concurrent acquirers cannot both read a stale count
    and both pass. The loser decrements back and is refused. A safety TTL is (re)set on
    every acquire so a leaked slot — a worker that dies before `release_import_slot` —
    expires rather than counting forever.
    """
    r = get_redis()
    key = _slot_key(org_key)
    count = await r.incr(key)
    await r.expire(key, _SLOT_TTL_SECONDS)
    if count > MAX_CONCURRENT_IMPORTS_PER_ORG:
        await r.decr(key)
        return False
    return True


async def release_import_slot(org_key: str) -> None:
    """Release a slot claimed by `acquire_import_slot`. Never drives the count negative."""
    r = get_redis()
    key = _slot_key(org_key)
    # A decrement that would cross zero means the slot already expired (TTL) or was already
    # released; clamp at zero rather than leave a negative that would over-grant later.
    current = await r.decr(key)
    if current < 0:
        await r.set(key, 0, ex=_SLOT_TTL_SECONDS)


__all__ = [
    "MAX_CONCURRENT_IMPORTS_PER_ORG",
    "BundleJobStatus",
    "ExportJobState",
    "ImportJobState",
    "acquire_import_slot",
    "create_export",
    "create_import",
    "get_export",
    "get_import",
    "mark_export_failed",
    "mark_export_ready",
    "mark_export_running",
    "mark_import_failed",
    "mark_import_ready",
    "mark_import_running",
    "release_import_slot",
]
