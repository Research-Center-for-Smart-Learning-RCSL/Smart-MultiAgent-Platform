"""Busy-lock retry for the graph build jobs (``graphrag_build``, ``knowmap_build``).

A build that finds its config lock held raises arq ``Retry`` and keeps its job id,
so a follow-up enqueue for the same work collapses onto it instead of being
swallowed by a failed result (``docs/tasks/2026-10-12-graph-build-busy-retry``).

arq has one ``max_tries`` for every way a job runs again: ``Retry``, a cancelled
job at shutdown, and a job whose worker died so its in-progress key expired. The
busy wait needs a large budget, but a build that kills its worker would then be
re-run that many times, each run spending the tenant's provider key. Busy tries
are therefore counted in Redis, and any other re-run is held to arq's default of
``BUILD_CRASH_MAX_TRIES`` attempts.
"""

from __future__ import annotations

import logging
import math
from typing import Any, NoReturn

from arq.worker import Retry

from contexts.knowledge.application.graphrag_builder import BUILD_BUSY_RETRY_DEFER_S
from shared_kernel.auth.clients import get_redis

_log = logging.getLogger(__name__)

# arq's own worker default, which these jobs had before the busy budget.
BUILD_CRASH_MAX_TRIES = 5

# Outlives arq's retry counter (88400 s), so a live job never loses its count.
_BUSY_TRIES_TTL_S = 2 * 24 * 60 * 60


def build_max_tries(timeout_s: int) -> int:
    """arq ``max_tries`` for a build job with this timeout.

    Enough busy retries to wait out a holder that runs to the same timeout, plus
    the crash allowance. arq runs try N only while N <= max_tries, so the busy
    share is ``ceil(timeout / defer)`` retries after the first try.
    """
    return math.ceil(timeout_s / BUILD_BUSY_RETRY_DEFER_S) + BUILD_CRASH_MAX_TRIES


def _busy_key(job_id: str) -> str:
    return f"graphrag:busy_tries:{job_id}"


async def retry_busy_build(ctx: dict[str, Any], *, job_name: str, config_id: str) -> NoReturn:
    """Record one busy try for this job and hand it back to arq to run later."""
    job_id = ctx.get("job_id")
    if job_id:
        try:
            redis = get_redis()
            key = _busy_key(str(job_id))
            await redis.incr(key)
            await redis.expire(key, _BUSY_TRIES_TTL_S)
        except Exception:
            # An undercount only tightens the crash allowance for this job.
            _log.warning("%s config=%s: busy try not counted", job_name, config_id, exc_info=True)
    _log.info("%s config=%s busy, retrying in %ds", job_name, config_id, BUILD_BUSY_RETRY_DEFER_S)
    raise Retry(defer=BUILD_BUSY_RETRY_DEFER_S)


async def crash_retries_exhausted(ctx: dict[str, Any]) -> bool:
    """Whether this job already ran ``BUILD_CRASH_MAX_TRIES`` times without being busy."""
    job_try = int(ctx.get("job_try") or 1)
    if job_try <= BUILD_CRASH_MAX_TRIES:
        return False
    job_id = ctx.get("job_id")
    if not job_id:
        return False
    try:
        raw = await get_redis().get(_busy_key(str(job_id)))
    except Exception:
        # Fail open: the busy budget still bounds the job, as it did before.
        _log.warning("busy-try count unreadable for job %s", job_id, exc_info=True)
        return False
    busy = int(raw or 0)
    return job_try - busy > BUILD_CRASH_MAX_TRIES


class BuildCrashLoopError(RuntimeError):
    """A build job was re-run after crashes more often than ``BUILD_CRASH_MAX_TRIES``."""


__all__ = [
    "BUILD_CRASH_MAX_TRIES",
    "BuildCrashLoopError",
    "build_max_tries",
    "crash_retries_exhausted",
    "retry_busy_build",
]
