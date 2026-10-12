"""A busy Knowledge Map build waits in arq instead of blocking its revision.

``docs/tasks/2026-10-12-graph-build-busy-retry`` AC-3, against a real Redis and a
real arq worker. The unit tier models enqueue as a mock and so cannot see the
defect: arq keeps a failed job's result under its id for ``keep_result``, and
``enqueue_job`` returns ``None`` for an id whose job *or* result key exists. A busy
build that failed therefore swallowed the follow-up enqueue for the same revision.
Only the build body is faked; the job id, the lock, the retry and the dedup are
the real ones.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from arq.connections import ArqRedis, RedisSettings, create_pool
from arq.constants import job_key_prefix, result_key_prefix, retry_key_prefix
from arq.jobs import Job
from arq.utils import timestamp_ms
from arq.worker import Function, Worker
from redis.asyncio import Redis

from app.workers.tasks import knowmap as kmod
from contexts.knowledge.application.knowmap_triggers import (
    EnqueueOutcome,
    enqueue_knowmap_build,
    knowmap_build_job_id,
)
from contexts.knowledge.domain.errors import GraphRagBuildBusy
from contexts.knowledge.domain.graphrag import BuildState
from contexts.knowledge.infrastructure.redis_lock import RedisBuildLockStore

pytestmark = pytest.mark.db


def _redis_settings() -> RedisSettings:
    from app.config.settings import get_settings

    return RedisSettings.from_dsn(get_settings().redis.dsn)


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    # Own client, not get_redis(): see tests/integration/test_draft_store_ttl.py.
    from app.config.settings import get_settings

    client: Redis = Redis.from_url(get_settings().redis.dsn)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def pool() -> AsyncIterator[ArqRedis]:
    p = await create_pool(_redis_settings())
    try:
        yield p
    finally:
        await p.aclose()


def _knowmap_build_registration() -> Function:
    from app.workers.main import WorkerSettings

    for entry in WorkerSettings.functions:
        if isinstance(entry, Function) and entry.name == "knowmap_build":
            return entry
    raise AssertionError("knowmap_build is not registered with arq.func()")


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def commit(self) -> None:
        return None


class _Closable:
    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def close(self) -> None:
        return None


async def _run_worker(queue: str, *, max_burst_jobs: int | None = None) -> None:
    """One burst of a real arq worker; raises if arq recorded a failed job."""
    worker = Worker(
        functions=[_knowmap_build_registration()],
        queue_name=queue,
        redis_settings=_redis_settings(),
        burst=True,
        poll_delay=0.05,
        handle_signals=False,
        keep_result=3600,
    )
    try:
        await worker.run_check(max_burst_jobs=max_burst_jobs)
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_follow_up_for_a_busy_revision_collapses_onto_the_waiting_job(
    redis: Redis, pool: ArqRedis
) -> None:
    cfg_id = uuid.uuid4()
    queue = f"test:knowmap-busy:{uuid.uuid4().hex}"
    job_id = knowmap_build_job_id(cfg_id, target_revision=2)
    lock_key = f"graphrag:lock:{cfg_id}"
    busy_key = f"graphrag:busy_tries:{job_id}"
    builds: list[int | None] = []

    class _Builder:
        """Takes the real per-config lock, so busy means the lock is really held."""

        def __init__(self, **_k: Any) -> None:
            self._locks = RedisBuildLockStore(redis)

        async def run(self, *, config_id: uuid.UUID, **_k: Any) -> Any:
            if not await self._locks.acquire(config_id, ttl_s=60):
                raise GraphRagBuildBusy(str(config_id))
            try:
                builds.append(2)
                return SimpleNamespace(state=BuildState.IDLE, triples_written=0, entities_written=0)
            finally:
                await self._locks.release(config_id)

    repo = AsyncMock()
    repo.get.return_value = SimpleNamespace(id=cfg_id)
    try:
        with (
            patch("contexts.knowledge.infrastructure.neo4j_driver.Neo4jAsyncDriver", _Closable),
            patch("qdrant_client.AsyncQdrantClient", _Closable),
            patch.object(kmod, "get_sessionmaker", return_value=_Session),
            patch.object(kmod, "KnowmapConfigRepository", return_value=repo),
            patch.object(kmod, "build_router", return_value=object()),
            patch.object(kmod, "_make_knowmap_embedder_factory", return_value=object()),
            patch.object(kmod, "GraphRagBuilder", _Builder),
            patch.object(kmod, "_finalize_build_revision", AsyncMock()),
        ):
            # Build A holds the config lock when job B (revision 2) starts.
            await redis.set(lock_key, "build-a", ex=60)
            outcome = await enqueue_knowmap_build(
                config_id=cfg_id, target_revision=2, pool=_queued(pool, queue)
            )
            assert outcome is EnqueueOutcome.QUEUED

            # One try of job B: it must stay queued under its id, not be stored
            # as a failed result.
            await _run_worker(queue, max_burst_jobs=1)
            assert await pool.zscore(queue, job_id) is not None
            assert not await redis.exists(result_key_prefix + job_id)

            # Build A finishes and its finalize enqueues the same revision.
            await redis.delete(lock_key)
            follow_up = await enqueue_knowmap_build(
                config_id=cfg_id, target_revision=2, pool=_queued(pool, queue)
            )
            assert follow_up is EnqueueOutcome.DEDUPED

            # Skip the rest of the busy defer; how long it is plays no part here.
            await pool.zadd(queue, {job_id: timestamp_ms()}, xx=True)
            await _run_worker(queue)

        assert builds == [2]
        info = await Job(job_id, pool, _queue_name=queue).result_info()
        assert info is not None
        assert info.success is True
        assert info.job_try == 2
        # The busy try was counted apart from the crash allowance.
        assert await redis.get(busy_key) == b"1"
    finally:
        await redis.delete(
            lock_key,
            busy_key,
            queue,
            job_key_prefix + job_id,
            result_key_prefix + job_id,
            retry_key_prefix + job_id,
        )


def _queued(pool: ArqRedis, queue: str) -> Any:
    """The shared pool, enqueueing onto this test's private queue."""

    class _Pool:
        async def enqueue_job(self, function: str, **kwargs: Any) -> Any:
            return await pool.enqueue_job(function, _queue_name=queue, **kwargs)

    return _Pool()
