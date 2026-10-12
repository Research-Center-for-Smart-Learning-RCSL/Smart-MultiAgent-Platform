"""Busy tries do not spend a graph build job's crash allowance.

arq applies one ``max_tries`` to busy retries and to re-runs after a crashed or
cancelled worker. The busy budget is large (``docs/tasks/2026-10-12-graph-build-
busy-retry``), so busy tries are counted apart and any other re-run is held to
``BUILD_CRASH_MAX_TRIES``, the arq default these jobs had before.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from arq.worker import Retry

import app.workers.tasks.build_retry as br
import app.workers.tasks.graphrag as graphrag
from app.workers.tasks import knowmap as kmod


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, ttl: int) -> bool:
        self.ttls[key] = ttl
        return True

    async def get(self, key: str) -> bytes | None:
        value = self.values.get(key)
        return None if value is None else str(value).encode()


@pytest.fixture
def redis() -> Any:
    fake = _FakeRedis()
    with patch.object(br, "get_redis", return_value=fake):
        yield fake


@pytest.mark.asyncio
async def test_a_busy_try_is_counted_against_the_job_and_retried(redis: _FakeRedis) -> None:
    with pytest.raises(Retry):
        await br.retry_busy_build({"job_id": "knowmap:build:c:2"}, job_name="knowmap_build", config_id="c")
    assert redis.values == {"graphrag:busy_tries:knowmap:build:c:2": 1}
    assert redis.ttls["graphrag:busy_tries:knowmap:build:c:2"] > 88400


@pytest.mark.asyncio
async def test_a_long_busy_wait_does_not_exhaust_the_crash_allowance(redis: _FakeRedis) -> None:
    ctx = {"job_id": "j"}
    for _ in range(40):
        with pytest.raises(Retry):
            await br.retry_busy_build(ctx, job_name="graphrag_build", config_id="c")
    # 40 busy tries, then a try that gets the lock: 41st run, no crash in between.
    assert await br.crash_retries_exhausted({"job_id": "j", "job_try": 41}) is False


@pytest.mark.asyncio
async def test_crash_re_runs_beyond_the_allowance_are_refused(redis: _FakeRedis) -> None:
    ctx = {"job_id": "j"}
    for _ in range(3):
        with pytest.raises(Retry):
            await br.retry_busy_build(ctx, job_name="graphrag_build", config_id="c")
    # 3 busy tries plus BUILD_CRASH_MAX_TRIES runs that never returned.
    at_limit = 3 + br.BUILD_CRASH_MAX_TRIES
    assert await br.crash_retries_exhausted({"job_id": "j", "job_try": at_limit}) is False
    assert await br.crash_retries_exhausted({"job_id": "j", "job_try": at_limit + 1}) is True


@pytest.mark.asyncio
async def test_without_any_busy_try_the_old_default_applies(redis: _FakeRedis) -> None:
    limit = br.BUILD_CRASH_MAX_TRIES
    assert await br.crash_retries_exhausted({"job_id": "j", "job_try": limit}) is False
    assert await br.crash_retries_exhausted({"job_id": "j", "job_try": limit + 1}) is True


@pytest.mark.asyncio
async def test_an_unreadable_count_does_not_stop_the_build() -> None:
    class _Down:
        async def get(self, key: str) -> Any:
            raise ConnectionError("redis down")

    with patch.object(br, "get_redis", return_value=_Down()):
        assert await br.crash_retries_exhausted({"job_id": "j", "job_try": 50}) is False


@pytest.mark.parametrize(
    ("job", "kwargs"),
    [
        (kmod.knowmap_build, {"target_revision": 2}),
        (graphrag.graphrag_build, {}),
    ],
)
@pytest.mark.asyncio
async def test_a_crash_looping_job_fails_before_building(
    redis: _FakeRedis, job: Any, kwargs: dict[str, Any]
) -> None:
    ctx = {"job_id": "j", "job_try": br.BUILD_CRASH_MAX_TRIES + 1}
    with (
        patch.object(kmod, "GraphRagBuilder", side_effect=AssertionError("built")),
        patch.object(graphrag, "_run_build", side_effect=AssertionError("built")),
        pytest.raises(br.BuildCrashLoopError),
    ):
        await job(ctx, config_id=str(uuid.uuid4()), **kwargs)
