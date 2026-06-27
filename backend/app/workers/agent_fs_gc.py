"""Nightly GC for per-agent file-system volumes and workspace objects.

Policy:
- The ``file`` built-in tool stores its state in a Docker named volume
  ``smap-agent-fs-{agent_id}``.
- Designer-uploaded workspace files live in MinIO bucket ``agent-workspace``
  under the ``{agent_id}/`` prefix.
- When an agent is soft-deleted, both are retained for **60 days** so
  admins can recover data before it is irreversibly purged.
- Every night this worker walks the ``agents`` table, finds rows whose
  ``deleted_at`` is older than 60 days, removes the matching volume AND
  the MinIO prefix.

Docker SDK import is lazy so the module imports cleanly without Docker
installed; the ``_main`` entrypoint aborts with a logged error if the daemon
is unreachable.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from contexts.agents.infrastructure import tables as t
from shared_kernel.db.session import get_sessionmaker

_log = logging.getLogger(__name__)

_RETENTION_DAYS = 60


_GC_BATCH_SIZE = 500


async def _list_purgeable_agent_ids(now: datetime) -> list[uuid.UUID]:
    cutoff = now - timedelta(days=_RETENTION_DAYS)
    maker = get_sessionmaker()
    ids: list[uuid.UUID] = []
    async with maker() as session:
        offset = 0
        while True:
            rows = (
                await session.execute(
                    sa.select(t.agents.c.id)
                    .where(
                        sa.and_(
                            t.agents.c.deleted_at.is_not(None),
                            t.agents.c.deleted_at < cutoff,
                        )
                    )
                    .order_by(t.agents.c.id)
                    .limit(_GC_BATCH_SIZE)
                    .offset(offset)
                )
            ).all()
            ids.extend(r[0] for r in rows)
            if len(rows) < _GC_BATCH_SIZE:
                break
            offset += _GC_BATCH_SIZE
    return ids


def _volume_name(agent_id: uuid.UUID) -> str:
    return f"smap-agent-fs-{agent_id}"


def _purge_volumes(names: Iterable[str]) -> int:
    """Remove every volume in ``names``. Returns the count actually removed.

    Volumes that don't exist are silently skipped; we run this nightly so a
    transient Docker hiccup is fine.
    """
    import docker

    client = docker.from_env()
    removed = 0
    for name in names:
        try:
            volume = client.volumes.get(name)
        except docker.errors.NotFound:
            continue
        try:
            volume.remove(force=True)
            removed += 1
            _log.info("agent_fs_gc: removed volume %s", name)
        except docker.errors.APIError as exc:
            _log.warning("agent_fs_gc: failed to remove %s: %s", name, exc)
    return removed


def _purge_workspace_objects(agent_ids: list[uuid.UUID]) -> int:
    """Remove MinIO workspace objects for deleted agents. Returns count removed."""
    from shared_kernel.storage import get_minio_client

    client = get_minio_client()
    bucket = client._cfg.bucket_agent_workspace
    removed = 0
    for agent_id in agent_ids:
        prefix = f"{agent_id}/"
        try:
            for obj in client.list_objects_sync(bucket, prefix=prefix):
                if obj.object_name:
                    client.remove_object_sync(bucket, obj.object_name)
                    removed += 1
        except Exception as exc:
            _log.warning("agent_fs_gc: failed to purge workspace objects for %s: %s", agent_id, exc)
    return removed


async def run_once(*, now: datetime | None = None) -> int:
    """Single pass — exposed for tests + manual invocation."""
    ts = now or datetime.now(tz=UTC)
    ids = await _list_purgeable_agent_ids(ts)
    if not ids:
        _log.info("agent_fs_gc: no volumes past retention")
        return 0
    names = [_volume_name(aid) for aid in ids]
    # _purge_volumes calls the sync Docker SDK; offload to a thread so it
    # does not block the async event loop during Docker API calls.
    vol_count = await asyncio.to_thread(_purge_volumes, names)
    obj_count = await asyncio.to_thread(_purge_workspace_objects, ids)
    if obj_count:
        _log.info("agent_fs_gc: removed %d workspace objects", obj_count)
    return vol_count + obj_count


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    await run_once()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())


__all__ = ["run_once"]
