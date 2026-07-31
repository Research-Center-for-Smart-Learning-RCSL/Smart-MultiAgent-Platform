"""AC-5 — a body that has lost its lock must be able to find out (F-23).

The heartbeat had two failure modes and was silent in both: an exception logged
and carried on (costing a full refresh interval each time, so three of them at
the default `ttl_s / 3` cadence spend exactly the TTL), and a rejected refresh
simply ended the loop. Either way the body ran on believing it still held a
lock that had lapsed. With `wakeup_agent`'s job timeout deliberately outlasting
the TTL, that is two turns serving one room.

See docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md (C4), R6.
The property this cannot show is that the key really expires -- a fake Redis has
no clock. That is `tests/wiring/test_turn_lock_expiry.py`, per Q-10.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

import contexts.agents.application.runtime.turn_engine as te
from shared_kernel.realtime import distributed_lock as dlock


class _FakeRedis:
    """A Redis whose heartbeat script can be made to fail or to reject."""

    def __init__(self, *, raises: int = 0, refresh_returns: int = 1) -> None:
        self.store: dict[str, str] = {}
        self._raises = raises
        self._refresh_returns = refresh_returns
        self.refreshes = 0

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        if script == dlock._HEARTBEAT_LUA:
            self.refreshes += 1
            if self._raises > 0:
                self._raises -= 1
                raise RuntimeError("redis unreachable")
            return self._refresh_returns
        self.store.pop(args[0], None)  # release
        return 1


class TestTheHandleReportsLoss:
    async def test_a_rejected_refresh_marks_the_lock_lost(self) -> None:
        """Fails today: the loop returned silently on `refreshed == 0`."""
        redis = _FakeRedis(refresh_returns=0)
        handle = dlock.LockHandle(acquired=True)

        await dlock._heartbeat_loop(redis, "k", "tok", ttl_s=300, interval_s=0.001, handle=handle)

        assert handle.held is False
        assert handle.lost_reason == "refresh_rejected"

    async def test_repeated_errors_mark_the_lock_lost(self) -> None:
        """Fails today: every exception logged and `continue`d, forever."""
        redis = _FakeRedis(raises=dlock.HEARTBEAT_MAX_FAILURES)
        handle = dlock.LockHandle(acquired=True)

        await dlock._heartbeat_loop(redis, "k", "tok", ttl_s=300, interval_s=0.004, handle=handle)

        assert handle.held is False
        assert handle.lost_reason == "heartbeat_unreachable"
        assert redis.refreshes == dlock.HEARTBEAT_MAX_FAILURES

    async def test_a_single_blip_does_not_lose_a_healthy_lock(self) -> None:
        """The other half of AC-5: a transient failure must not abort a healthy
        turn. One error, then success, and the loop keeps refreshing."""
        redis = _FakeRedis(raises=1)
        handle = dlock.LockHandle(acquired=True)

        task = asyncio.create_task(
            dlock._heartbeat_loop(redis, "k", "tok", ttl_s=300, interval_s=0.004, handle=handle)
        )
        await asyncio.sleep(0.06)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert handle.held is True
        assert redis.refreshes > 1

    async def test_a_failed_refresh_is_retried_inside_the_interval(self) -> None:
        """Waiting a whole interval per failure is what made three failures cost
        exactly the TTL at the default cadence."""
        redis = _FakeRedis(raises=2)
        handle = dlock.LockHandle(acquired=True)
        interval = 0.4

        task = asyncio.create_task(
            dlock._heartbeat_loop(redis, "k", "tok", ttl_s=300, interval_s=interval, handle=handle)
        )
        # The first attempt costs a full interval; the two retries cost
        # `interval / (HEARTBEAT_MAX_FAILURES + 1)` each, so all three land
        # inside 0.7s. Had each retry waited another full interval, the third
        # would not arrive until 1.2s.
        await asyncio.sleep(interval + 3 * (interval / (dlock.HEARTBEAT_MAX_FAILURES + 1)))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert redis.refreshes >= 3


class TestTheHandleIsADropInForTheBool:
    async def test_it_is_truthy_when_acquired_and_falsy_when_not(self) -> None:
        assert bool(dlock.LockHandle(acquired=True)) is True
        assert bool(dlock.LockHandle(acquired=False)) is False

    async def test_losing_the_lock_does_not_retroactively_unacquire_it(self) -> None:
        """`bool()` answers "did we get it", `held` answers "do we still have
        it". Every existing call site asks the first at the top of its block, so
        conflating them would change what those sites mean."""
        handle = dlock.LockHandle(acquired=True)
        handle.mark_lost("refresh_rejected")

        assert bool(handle) is True
        assert handle.acquired is True
        assert handle.held is False

    async def test_the_context_manager_yields_a_handle(self) -> None:
        redis = _FakeRedis()

        async with dlock.distributed_lock("k", ttl_s=300, redis=redis) as handle:
            assert isinstance(handle, dlock.LockHandle)
            assert handle.held is True

    async def test_a_contended_lock_yields_a_falsy_handle(self) -> None:
        redis = _FakeRedis()
        redis.store["k"] = "someone-else"

        async with dlock.distributed_lock("k", ttl_s=300, redis=redis) as handle:
            assert not handle
            assert handle.held is False


class TestTheTurnConsumesTheLiveness:
    async def test_a_held_lock_produces_no_cancel_check(self) -> None:
        """A turn with no lock to watch keeps the previous no-check behaviour
        rather than paying for a predicate that can never fire."""
        assert te._lock_liveness_check(None) is None

    async def test_the_check_fires_only_once_the_lock_is_lost(self) -> None:
        handle = dlock.LockHandle(acquired=True)
        check = te._lock_liveness_check(handle)
        assert check is not None

        assert await check() is False
        handle.mark_lost("refresh_rejected")
        assert await check() is True

    async def test_the_turn_loop_stops_at_a_round_boundary_when_the_lock_is_lost(self) -> None:
        """`_stream_with_tools` already had the extension point; before C4 the
        room path passed no `cancel_check` at all, so nothing could use it."""
        handle = dlock.LockHandle(acquired=True)
        handle.mark_lost("refresh_rejected")

        engine = te.TurnEngine.__new__(te.TurnEngine)

        class _Router:
            async def call_stream(self, **kw: Any) -> Any:
                raise AssertionError("the provider was called after the lock was lost")
                yield  # pragma: no cover -- makes this an async generator

        engine._router = _Router()  # type: ignore[attr-defined]

        with pytest.raises(te._TurnCancelled):
            await engine._stream_with_tools(
                agent=_agent(),
                chatroom_id=uuid.uuid4(),
                parent_agent_id=None,
                system_text="sys",
                messages=[{"role": "user", "content": "hi"}],
                provider=_provider(),
                model="m",
                registry=_Registry(),
                room="room",
                cancel_check=te._lock_liveness_check(handle),
            )


class _Registry:
    def specs(self) -> list[Any]:
        return []


def _agent() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        id=uuid.uuid4(), key_group_id=uuid.uuid4(), effort=None, temperature=None, top_p=None, seed=None
    )


def _provider() -> Any:
    from contexts.keys.domain.providers import ApiKeyProvider

    return ApiKeyProvider.CLAUDE
