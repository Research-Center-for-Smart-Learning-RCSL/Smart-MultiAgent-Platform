"""Recorded room arrivals and the 30-minute per-person window ([R13.79], AC-2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

import contexts.conversation.infrastructure.room_joins as room_joins


class _FakeRedis:
    """The subset room_joins uses, with key expiry on a controllable clock."""

    def __init__(self) -> None:
        self.clock = 0.0
        self.strings: dict[str, tuple[str, float]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        held = self.strings.get(key)
        if nx and held is not None and held[1] > self.clock:
            return None
        self.strings[key] = (value, self.clock + (ex or 10**9))
        return True

    async def zrangebyscore(self, key, lo, hi, *, start, num, withscores):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1])
        hi_v = float("inf") if hi == "+inf" else float(hi)
        hits = [(m, s) for m, s in items if float(lo) <= s <= hi_v]
        return hits[start : start + num]

    def pipeline(self, transaction: bool = False) -> _FakePipe:
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, r: _FakeRedis) -> None:
        self._r = r
        self._ops: list = []

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self._ops.append(lambda: self._r.zsets.setdefault(key, {}).update(mapping))

    def zremrangebyrank(self, key: str, start: int, stop: int) -> None:
        def _trim() -> None:
            z = self._r.zsets.get(key, {})
            ordered = sorted(z, key=lambda m: z[m])
            # Redis semantics for start=0, stop=-(N+1): drop all but the newest N.
            keep = len(ordered) + stop + 1
            for member in ordered[: max(0, keep)]:
                del z[member]

        self._ops.append(_trim)

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(lambda: self._r.ttls.__setitem__(key, ttl))

    async def execute(self) -> list:
        return [op() for op in self._ops]


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    r = _FakeRedis()
    monkeypatch.setattr(room_joins, "get_redis", lambda: r)
    return r


_T0 = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)


async def test_refresh_inside_the_window_records_nothing(redis: _FakeRedis) -> None:
    room, who = uuid.uuid4(), uuid.uuid4()

    assert await room_joins.record_join(room, who, at=_T0) is True
    redis.clock += 29 * 60
    assert await room_joins.record_join(room, who, at=_T0 + timedelta(minutes=29)) is False

    joins = await room_joins.recent_joins(room, since=_T0 - timedelta(hours=1))
    assert joins == [(who, _T0)]


async def test_reconnection_after_the_window_is_a_new_join(redis: _FakeRedis) -> None:
    room, who = uuid.uuid4(), uuid.uuid4()
    later = _T0 + timedelta(minutes=31)

    assert await room_joins.record_join(room, who, at=_T0) is True
    redis.clock += 31 * 60
    assert await room_joins.record_join(room, who, at=later) is True

    joins = await room_joins.recent_joins(room, since=_T0 - timedelta(hours=1))
    assert joins == [(who, later)]


async def test_window_is_per_room_and_per_person(redis: _FakeRedis) -> None:
    room_a, room_b, p1, p2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    assert await room_joins.record_join(room_a, p1, at=_T0) is True
    assert await room_joins.record_join(room_b, p1, at=_T0) is True
    assert await room_joins.record_join(room_a, p2, at=_T0) is True


async def test_recent_joins_filters_by_since_and_orders_oldest_first(redis: _FakeRedis) -> None:
    room = uuid.uuid4()
    early, mid, late = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await room_joins.record_join(room, early, at=_T0)
    await room_joins.record_join(room, late, at=_T0 + timedelta(minutes=10))
    await room_joins.record_join(room, mid, at=_T0 + timedelta(minutes=5))

    joins = await room_joins.recent_joins(room, since=_T0 + timedelta(minutes=1))
    assert [who for who, _ in joins] == [mid, late]


async def test_set_is_trimmed_to_the_newest_joins_and_expires(redis: _FakeRedis) -> None:
    room = uuid.uuid4()
    people = [uuid.uuid4() for _ in range(room_joins.MAX_RECORDED_JOINS + 5)]
    for i, who in enumerate(people):
        await room_joins.record_join(room, who, at=_T0 + timedelta(seconds=i))

    joins = await room_joins.recent_joins(room, since=_T0 - timedelta(hours=1))
    assert [who for who, _ in joins] == people[5:]
    assert redis.ttls[f"ws:joins:{room}"] == 6 * 3600


async def test_a_failed_join_set_write_releases_the_window(
    redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise the arrival, and every reconnect inside the window, is lost to the join set."""
    room, who = uuid.uuid4(), uuid.uuid4()

    async def _fail(_self: _FakePipe) -> list:
        raise ConnectionError("redis down")

    async def _delete(key: str) -> int:
        return 1 if redis.strings.pop(key, None) else 0

    redis.delete = _delete  # type: ignore[attr-defined]
    monkeypatch.setattr(_FakePipe, "execute", _fail)
    with pytest.raises(ConnectionError):
        await room_joins.record_join(room, who, at=_T0)
    monkeypatch.undo()
    monkeypatch.setattr(room_joins, "get_redis", lambda: redis)

    assert await room_joins.record_join(room, who, at=_T0) is True
    assert await room_joins.recent_joins(room, since=_T0) == [(who, _T0)]
