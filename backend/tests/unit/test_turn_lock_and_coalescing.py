"""The coalesced-trigger protocol: atomic, and honest about what it knows.

Four defects live in these ~90 lines (F-22, F-39, F-30, plus one neither audit
recorded):

- the pop read its two keys with two unpipelined round-trips, so a concurrent
  popper or marker could interleave between them and cost the triggering
  message id;
- the mark swallowed its own write failure and returned ``None`` either way;
- the pop returned ``None`` for both "nothing parked" and "Redis raised";
- and ``run_turn`` read that conflated ``None`` as proof that somebody else had
  already enqueued a follow-up, reporting ``skipped/locked`` for a message
  nobody was going to answer.

See docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md (C5), R4/R5/R7.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

import contexts.agents.application.runtime.turn_engine as te


class _FakeRedis:
    """Enough Redis to run the two coalescing scripts, plus a command log.

    ``eval`` interprets the two known scripts rather than parsing Lua. The point
    of the fake is to make atomicity *observable* — one command per operation,
    both keys handled inside it — not to be a Redis. That is also why every
    other command it might be called with is recorded rather than implemented:
    the command log is the assertion surface.
    """

    def __init__(
        self,
        *,
        fail_pop: bool = False,
        fail_mark: bool = False,
        drop_marks: bool = False,
    ) -> None:
        self.store: dict[str, str] = {}
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self._fail_pop = fail_pop
        self._fail_mark = fail_mark
        # Stands in for a competing popper: the mark lands and is immediately
        # taken by the previous lock holder's post-release drain, which is the
        # only way a retry legitimately finds its own mark ABSENT.
        self._drop_marks = drop_marks

    async def eval(self, script: str, numkeys: int, *args: str) -> Any:
        keys, argv = tuple(args[:numkeys]), tuple(args[numkeys:])
        self.calls.append(("eval", keys))
        if script == te._POP_QUEUED_LUA:
            if self._fail_pop:
                raise RuntimeError("redis unreachable")
            return [self.store.pop(keys[0], ""), self.store.pop(keys[1], "")]
        if script == te._MARK_QUEUED_LUA:
            if self._fail_mark:
                raise RuntimeError("redis unreachable")
            self.store.setdefault(keys[0], argv[0])  # NX — first trigger wins
            if argv[1]:
                self.store[keys[1]] = argv[1]  # plain SET — last id wins
            if self._drop_marks:
                self.store.clear()
            return 1
        raise AssertionError(f"unexpected script: {script!r}")

    async def getdel(self, key: str) -> None:
        self.calls.append(("getdel", (key,)))

    async def set(self, key: str, *a: Any, **k: Any) -> bool:
        self.calls.append(("set", (key,)))
        return True

    @property
    def commands(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr("shared_kernel.auth.clients.get_redis", lambda: fake)
    return fake


def _use(monkeypatch: pytest.MonkeyPatch, fake: _FakeRedis) -> _FakeRedis:
    monkeypatch.setattr("shared_kernel.auth.clients.get_redis", lambda: fake)
    return fake


class TestThePopIsOneAtomicOperation:
    """AC-6."""

    async def test_pop_issues_a_single_round_trip(self, redis: _FakeRedis) -> None:
        """Fails against the two unpipelined GETDELs this replaced."""
        agent_id, room = uuid.uuid4(), uuid.uuid4()

        await te._pop_queued_trigger(agent_id, room)

        assert redis.commands == ["eval"]

    async def test_the_single_round_trip_covers_both_keys(self, redis: _FakeRedis) -> None:
        """What atomicity means here: no interleave point exists between the
        trigger key and its message-id key, because they are read and cleared
        inside one command."""
        agent_id, room = uuid.uuid4(), uuid.uuid4()

        await te._pop_queued_trigger(agent_id, room)

        ((_, keys),) = redis.calls
        assert set(keys) == {
            te._queued_trigger_key(agent_id, room),
            te._queued_trigger_message_key(agent_id, room),
        }

    async def test_mark_issues_a_single_round_trip(self, redis: _FakeRedis) -> None:
        agent_id, room = uuid.uuid4(), uuid.uuid4()

        await te._mark_trigger_queued(agent_id, room, "mention", uuid.uuid4())

        assert redis.commands == ["eval"]

    async def test_trigger_and_message_id_come_back_together(self, redis: _FakeRedis) -> None:
        """R7: the id can no longer be lost independently of the trigger."""
        agent_id, room, mid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await te._mark_trigger_queued(agent_id, room, "mention", mid)

        popped = await te._pop_queued_trigger(agent_id, room)

        assert popped.state is te.TriggerPopState.PARKED
        assert popped.trigger == "mention"
        assert popped.message_id == mid
        assert redis.store == {}, "both keys must be cleared by the pop"


class TestTheProtocolKeepsItsWriteSemantics:
    async def test_first_trigger_wins_and_the_latest_message_id_wins(self, redis: _FakeRedis) -> None:
        agent_id, room = uuid.uuid4(), uuid.uuid4()
        first, second = uuid.uuid4(), uuid.uuid4()

        await te._mark_trigger_queued(agent_id, room, "mention", first)
        await te._mark_trigger_queued(agent_id, room, "every_n_messages", second)

        popped = await te._pop_queued_trigger(agent_id, room)
        assert popped.trigger == "mention"
        assert popped.message_id == second

    async def test_a_mark_without_a_message_id_leaves_the_id_key_alone(self, redis: _FakeRedis) -> None:
        agent_id, room = uuid.uuid4(), uuid.uuid4()
        mid = uuid.uuid4()

        await te._mark_trigger_queued(agent_id, room, "mention", mid)
        await te._mark_trigger_queued(agent_id, room, "silence_minutes", None)

        popped = await te._pop_queued_trigger(agent_id, room)
        assert popped.message_id == mid


class TestTheProtocolSaysWhatItKnows:
    """AC-7's first half — the helpers stop conflating outcomes."""

    async def test_mark_reports_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fails today: the bare `except` returned None on success and failure."""
        _use(monkeypatch, _FakeRedis(fail_mark=True))

        assert await te._mark_trigger_queued(uuid.uuid4(), uuid.uuid4(), "mention") is False

    async def test_mark_reports_success(self, redis: _FakeRedis) -> None:
        assert await te._mark_trigger_queued(uuid.uuid4(), uuid.uuid4(), "mention") is True

    async def test_pop_distinguishes_absent_from_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fails today: both returned a bare None."""
        _use(monkeypatch, _FakeRedis())
        absent = await te._pop_queued_trigger(uuid.uuid4(), uuid.uuid4())

        _use(monkeypatch, _FakeRedis(fail_pop=True))
        broken = await te._pop_queued_trigger(uuid.uuid4(), uuid.uuid4())

        assert absent.state is te.TriggerPopState.ABSENT
        assert broken.state is te.TriggerPopState.UNKNOWN


# ---------------------------------------------------------------------------
# run_turn's reading of those outcomes (AC-7's second half)
# ---------------------------------------------------------------------------


def _wire_run_turn(
    monkeypatch: pytest.MonkeyPatch,
    *,
    acquires: list[bool],
    fake: _FakeRedis,
) -> tuple[Any, list[str]]:
    """A `run_turn` whose lock acquisition is scripted and whose `_run_locked`
    only records that it ran. Returns `(engine, ran)`."""
    _use(monkeypatch, fake)
    ran: list[str] = []
    remaining = list(acquires)

    class _Lock:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> bool:
            return remaining.pop(0) if remaining else False

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(te, "turn_lock", _Lock)

    async def _enqueue(*args: Any) -> None:
        return None

    monkeypatch.setattr("shared_kernel.queue.enqueue", _enqueue)

    engine = te.TurnEngine.__new__(te.TurnEngine)

    async def _locked(*, trigger: str, **kw: Any) -> te.TurnResult:
        ran.append(trigger)
        return te.TurnResult(status="completed")

    engine._run_locked = _locked  # type: ignore[attr-defined]
    return engine, ran


class TestRunTurnStopsReportingLockedForADroppedMessage:
    async def test_a_failed_mark_is_reported_as_a_drop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """R4 — fails today: the message is dropped and audited as `locked`."""
        engine, ran = _wire_run_turn(monkeypatch, acquires=[False, False], fake=_FakeRedis(fail_mark=True))

        result = await engine.run_turn(agent_id=uuid.uuid4(), chatroom_id=uuid.uuid4(), trigger="mention")

        assert result.status == "skipped"
        assert result.reason == "coalesce_failed"
        assert ran == []

    async def test_a_durable_mark_is_still_reported_as_locked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The unchanged case: the holder really will answer this message."""
        engine, ran = _wire_run_turn(monkeypatch, acquires=[False, False], fake=_FakeRedis())

        result = await engine.run_turn(agent_id=uuid.uuid4(), chatroom_id=uuid.uuid4(), trigger="mention")

        assert (result.status, result.reason) == ("skipped", "locked")
        assert ran == []

    async def test_a_failed_pop_on_the_retry_still_runs_the_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R5, the defect neither audit recorded: a transient Redis read failure
        used to `break` out of the retry and report `locked`, dropping the
        message from the *pop* side."""
        engine, ran = _wire_run_turn(monkeypatch, acquires=[False, True], fake=_FakeRedis(fail_pop=True))

        result = await engine.run_turn(agent_id=uuid.uuid4(), chatroom_id=uuid.uuid4(), trigger="mention")

        assert result.status == "completed"
        assert ran == ["mention"]

    async def test_an_absent_mark_on_the_retry_still_defers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The case the `break` was written for, and the only one it now covers:
        the previous holder popped our mark and has already enqueued a follow-up,
        so running here would duplicate that turn."""
        engine, ran = _wire_run_turn(monkeypatch, acquires=[False, True], fake=_FakeRedis(drop_marks=True))

        result = await engine.run_turn(agent_id=uuid.uuid4(), chatroom_id=uuid.uuid4(), trigger="mention")

        assert (result.status, result.reason) == ("skipped", "locked")
        assert ran == []

    async def test_a_parked_mark_on_the_retry_is_served_here(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeRedis()
        engine, ran = _wire_run_turn(monkeypatch, acquires=[False, True], fake=fake)

        result = await engine.run_turn(agent_id=uuid.uuid4(), chatroom_id=uuid.uuid4(), trigger="mention")

        assert result.status == "completed"
        assert ran == ["mention"]
