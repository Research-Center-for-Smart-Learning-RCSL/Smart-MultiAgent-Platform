"""R6 — real-Redis proof that a lapsed lock is actually re-acquirable.

The unit tests in ``tests/unit/test_distributed_lock_liveness.py`` cover both
heartbeat failure modes, but neither can show the property that makes them
matter: that the key really expires and a second holder really can take it
while the first context manager is still open. A fake Redis has no clock, and
``fakeredis`` is not a dependency (Q-10), so this test lives in the wiring tier,
which already provisions a real Redis and already disposes the process-global
client per test.

Spec: docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md (C4), §7 R6.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from contexts.agents.infrastructure.turn_lock import turn_lock, turn_lock_key
from shared_kernel.auth.clients import get_redis
from shared_kernel.realtime.distributed_lock import acquire_lock, distributed_lock

pytestmark = pytest.mark.wiring


async def test_a_lapsed_lock_is_re_acquirable_and_the_holder_is_told() -> None:
    """The two halves of F-23 in one run: a second acquire succeeds while the
    first block is still open, and the first holder's handle says so.

    `heartbeat_interval_s` is set well past the TTL so the lock genuinely
    lapses -- that is the state a turn whose worker is wedged, or whose job
    timeout outlives the TTL, actually reaches.
    """
    key = f"itest:lock:{uuid.uuid4()}"
    redis = get_redis()
    try:
        async with distributed_lock(key, ttl_s=1, heartbeat_interval_s=30) as first:
            assert first.held is True
            # Past the TTL, with no heartbeat due: the key is gone.
            await asyncio.sleep(1.5)
            second = await acquire_lock(key, ttl_s=5)
            assert second is not None, "a lapsed lock must be re-acquirable"

            # The first holder is now running beside a second one. Its next
            # refresh finds a different token and marks the handle lost, which
            # is what a turn checks at its round boundary.
            await asyncio.sleep(0)
            from shared_kernel.realtime.distributed_lock import _heartbeat_loop

            await _heartbeat_loop(redis, key, "not-the-current-token", ttl_s=1, interval_s=0.01, handle=first)
            assert first.held is False
            assert first.lost_reason == "refresh_rejected"
    finally:
        await redis.delete(key)


async def test_a_healthy_heartbeat_keeps_the_lock_past_its_ttl() -> None:
    """The control: with the heartbeat running, a TTL shorter than the body is
    exactly the case the refresh exists for, and the lock must survive it."""
    key = f"itest:lock:{uuid.uuid4()}"
    redis = get_redis()
    try:
        async with distributed_lock(key, ttl_s=2, heartbeat_interval_s=0.2) as handle:
            await asyncio.sleep(2.5)
            assert handle.held is True
            assert await acquire_lock(key, ttl_s=5) is None, "the lock must still be held"
        assert await redis.get(key) is None, "the block must release on exit"
    finally:
        await redis.delete(key)


async def test_the_turn_lock_wrapper_yields_the_same_handle() -> None:
    agent_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    key = turn_lock_key(agent_id, chatroom_id)
    redis = get_redis()
    try:
        async with turn_lock(agent_id, chatroom_id, ttl_s=5) as handle:
            assert handle.held is True
            async with turn_lock(agent_id, chatroom_id, ttl_s=5) as contender:
                assert not contender
                assert contender.held is False
    finally:
        await redis.delete(key)
