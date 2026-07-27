"""Presence refcount semantics (R13.19) — multi-tab and last-leave behaviour."""

from __future__ import annotations

import uuid

import pytest

import contexts.conversation.infrastructure.presence as presence_mod
from contexts.conversation.infrastructure.presence import PresenceTracker


class _FakeRedis:
    """In-memory subset of the Redis SET surface presence.py uses."""

    def __init__(self) -> None:
        self.sets: dict[str, set[str]] = {}

    async def sadd(self, key: str, member: str) -> int:
        s = self.sets.setdefault(key, set())
        if member in s:
            return 0
        s.add(member)
        return 1

    async def srem(self, key: str, member: str) -> int:
        s = self.sets.get(key)
        if not s or member not in s:
            return 0
        s.discard(member)
        if not s:
            del self.sets[key]
        return 1

    async def scard(self, key: str) -> int:
        return len(self.sets.get(key, set()))

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def delete(self, key: str) -> None:
        self.sets.pop(key, None)

    async def exists(self, key: str) -> int:
        return 1 if key in self.sets else 0

    async def scan_iter(self, match: str, count: int = 0):
        prefix = match.rstrip("*")
        for key in list(self.sets.keys()):
            if key.startswith(prefix):
                yield key

    async def eval(self, script: str, numkeys: int, *args):
        # Emulate the presence JOIN (SADD+SCARD) and LEAVE (SREM+SCARD+DEL) scripts.
        key, member = args[0], args[1]
        if "SADD" in script:
            self.sets.setdefault(key, set()).add(member)
            return len(self.sets[key])
        s = self.sets.get(key)
        if s and member in s:
            s.discard(member)
        n = len(self.sets.get(key, set()))
        if n == 0:
            self.sets.pop(key, None)
        return n

    def pipeline(self, transaction: bool = False) -> _FakePipe:
        return _FakePipe(self)


class _FakePipe:
    """Buffers SADD/SREM/EXPIRE and applies them on execute (no transaction)."""

    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list = []

    def sadd(self, key: str, member: str) -> None:
        self._ops.append(("sadd", key, member))

    def srem(self, key: str, member: str) -> None:
        self._ops.append(("srem", key, member))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", key, ttl))

    def exists(self, key: str) -> None:
        self._ops.append(("exists", key))

    async def execute(self) -> list:
        results: list = []
        for op in self._ops:
            if op[0] == "sadd":
                results.append(await self._redis.sadd(op[1], op[2]))
            elif op[0] == "srem":
                results.append(await self._redis.srem(op[1], op[2]))
            elif op[0] == "expire":
                results.append(None)
            elif op[0] == "exists":
                results.append(await self._redis.exists(op[1]))
        self._ops = []
        return results


@pytest.fixture
def fake_redis(monkeypatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(presence_mod, "get_redis", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_multi_tab_announces_once_and_leaves_on_last(fake_redis: _FakeRedis) -> None:
    p = PresenceTracker()
    room = uuid.uuid4()
    user = uuid.uuid4()
    c1, c2 = uuid.uuid4(), uuid.uuid4()

    added, roster_size = await p.join(room_id=room, user_id=user, connection_id=c1)
    assert (added, roster_size) == (True, 1)
    # Second tab: same user, must NOT re-announce; roster stays size 1.
    added, roster_size = await p.join(room_id=room, user_id=user, connection_id=c2)
    assert (added, roster_size) == (False, 1)
    assert await p.list_room(room) == [user]

    # Closing the first tab while the second is live must NOT drop the user.
    left, roster_size = await p.leave(room_id=room, user_id=user, connection_id=c1)
    assert left is False
    assert await p.list_room(room) == [user]

    # Closing the last tab fully removes the user.
    left, roster_size = await p.leave(room_id=room, user_id=user, connection_id=c2)
    assert (left, roster_size) == (True, 0)
    assert await p.list_room(room) == []


@pytest.mark.asyncio
async def test_last_leave_reports_empty_roster_despite_a_ghost_member(fake_redis: _FakeRedis) -> None:
    """F-5: a ghost member (conns key expired without a clean leave) must not
    inflate the cardinality the last real leaver observes."""
    p = PresenceTracker()
    room = uuid.uuid4()
    stale_user, live_user = uuid.uuid4(), uuid.uuid4()
    await p.join(room_id=room, user_id=stale_user, connection_id=uuid.uuid4())
    live_conn = uuid.uuid4()
    await p.join(room_id=room, user_id=live_user, connection_id=live_conn)
    fake_redis.sets.pop(f"ws:presence:{room}:{stale_user}:conns", None)

    left, roster_size = await p.leave(room_id=room, user_id=live_user, connection_id=live_conn)
    assert (left, roster_size) == (True, 0)


@pytest.mark.asyncio
async def test_list_room_omits_and_evicts_a_member_with_no_live_connection(fake_redis: _FakeRedis) -> None:
    """F-5: `list_room` must not return a ghost member, and must evict it from
    both the roster and the reverse index as it discovers it."""
    p = PresenceTracker()
    room = uuid.uuid4()
    stale_user, live_user = uuid.uuid4(), uuid.uuid4()
    await p.join(room_id=room, user_id=stale_user, connection_id=uuid.uuid4())
    await p.join(room_id=room, user_id=live_user, connection_id=uuid.uuid4())
    fake_redis.sets.pop(f"ws:presence:{room}:{stale_user}:conns", None)

    assert set(await p.list_room(room)) == {live_user}
    assert str(stale_user) not in fake_redis.sets.get(f"ws:presence:{room}", set())
    assert f"ws:user:{stale_user}:rooms" not in fake_redis.sets


@pytest.mark.asyncio
async def test_multi_tab_leave_does_not_reconcile(fake_redis: _FakeRedis, monkeypatch) -> None:
    """Guard: the multi-tab early return must stay untouched by A1 -- a leave
    that is not the user's last connection must not trigger reconciliation."""
    p = PresenceTracker()
    room, user = uuid.uuid4(), uuid.uuid4()
    c1, c2 = uuid.uuid4(), uuid.uuid4()
    await p.join(room_id=room, user_id=user, connection_id=c1)
    await p.join(room_id=room, user_id=user, connection_id=c2)

    exists_calls = 0
    orig_exists = fake_redis.exists

    async def _counting_exists(key: str) -> int:
        nonlocal exists_calls
        exists_calls += 1
        return await orig_exists(key)

    monkeypatch.setattr(fake_redis, "exists", _counting_exists)

    left, roster_size = await p.leave(room_id=room, user_id=user, connection_id=c1)
    assert (left, roster_size) == (False, -1)
    assert exists_calls == 0


@pytest.mark.asyncio
async def test_scrub_removes_user_without_conns(fake_redis: _FakeRedis) -> None:
    p = PresenceTracker()
    room = uuid.uuid4()
    user = uuid.uuid4()
    await p.join(room_id=room, user_id=user, connection_id=uuid.uuid4())

    # Simulate the conns SET expiring (connection died without clean leave).
    fake_redis.sets.pop(f"ws:presence:{room}:{user}:conns", None)

    removed, emptied_rooms = await presence_mod.scrub_stale_presence()
    assert removed == 1
    assert emptied_rooms == {room}
    assert await p.list_room(room) == []
    assert f"ws:user:{user}:rooms" not in fake_redis.sets


@pytest.mark.asyncio
async def test_scrub_keeps_live_user(fake_redis: _FakeRedis) -> None:
    p = PresenceTracker()
    room = uuid.uuid4()
    user = uuid.uuid4()
    await p.join(room_id=room, user_id=user, connection_id=uuid.uuid4())

    removed, emptied_rooms = await presence_mod.scrub_stale_presence()
    assert removed == 0
    assert emptied_rooms == set()
    assert await p.list_room(room) == [user]


@pytest.mark.asyncio
async def test_scrub_does_not_report_room_still_holding_a_live_user(fake_redis: _FakeRedis) -> None:
    """B1: a room with one stale and one live member is not "emptied" by the
    sweep — the retention hook must not pause silence timers for a room that
    still has a live user in it."""
    p = PresenceTracker()
    room = uuid.uuid4()
    stale_user = uuid.uuid4()
    live_user = uuid.uuid4()
    await p.join(room_id=room, user_id=stale_user, connection_id=uuid.uuid4())
    await p.join(room_id=room, user_id=live_user, connection_id=uuid.uuid4())
    fake_redis.sets.pop(f"ws:presence:{room}:{stale_user}:conns", None)

    removed, emptied_rooms = await presence_mod.scrub_stale_presence()
    assert removed == 1
    assert emptied_rooms == set()
    assert await p.list_room(room) == [live_user]


# ---------------------------------------------------------------------------
# Typing refcount — the same multi-tab problem, on a different signal.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typing_retracts_only_when_the_last_typing_connection_stops(
    fake_redis: _FakeRedis,
) -> None:
    # `typing.stop` carries a user id and nothing else, so it is read as "this
    # user stopped typing". Only the last of a user's typing connections may
    # publish it; otherwise one tab ending its burst blanks the indicator for a
    # sibling that is still mid-burst.
    p = PresenceTracker()
    room, user = uuid.uuid4(), uuid.uuid4()
    c1, c2 = uuid.uuid4(), uuid.uuid4()

    assert await p.typing_start(room_id=room, user_id=user, connection_id=c1) is True
    assert await p.typing_start(room_id=room, user_id=user, connection_id=c2) is False

    assert await p.typing_stop(room_id=room, user_id=user, connection_id=c1) is False
    assert await p.typing_stop(room_id=room, user_id=user, connection_id=c2) is True


@pytest.mark.asyncio
async def test_typing_state_is_scoped_per_user_and_room(fake_redis: _FakeRedis) -> None:
    p = PresenceTracker()
    room_a, room_b = uuid.uuid4(), uuid.uuid4()
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    conn = uuid.uuid4()

    await p.typing_start(room_id=room_a, user_id=user_a, connection_id=conn)

    # A different user, and the same user in a different room, are unaffected.
    assert await p.typing_start(room_id=room_a, user_id=user_b, connection_id=conn) is True
    assert await p.typing_start(room_id=room_b, user_id=user_a, connection_id=conn) is True


@pytest.mark.asyncio
async def test_typing_stop_without_a_start_still_reports_last(fake_redis: _FakeRedis) -> None:
    # A burst whose assertion lapsed (long burst, expired TTL) must still be
    # able to retract: reporting False here would pin the indicator on, which
    # is the F-18 failure mode this must not reintroduce.
    p = PresenceTracker()

    assert await p.typing_stop(room_id=uuid.uuid4(), user_id=uuid.uuid4(), connection_id=uuid.uuid4()) is True


@pytest.mark.asyncio
async def test_typing_keys_are_not_swept_by_the_stale_presence_scrub(fake_redis: _FakeRedis) -> None:
    # `scrub_stale_presence` scans `ws:presence:*` and tells roster keys from
    # conns keys by counting ':'. A typing key under that prefix would be
    # misread as a room roster; it deliberately lives under `ws:typing:`.
    p = PresenceTracker()
    room, user = uuid.uuid4(), uuid.uuid4()
    await p.join(room_id=room, user_id=user, connection_id=uuid.uuid4())
    await p.typing_start(room_id=room, user_id=user, connection_id=uuid.uuid4())

    removed, emptied = await presence_mod.scrub_stale_presence()

    assert (removed, emptied) == (0, set())
    assert f"ws:typing:{room}:{user}" in fake_redis.sets
