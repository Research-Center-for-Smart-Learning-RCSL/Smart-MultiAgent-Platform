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

    assert await p.join(room_id=room, user_id=user, connection_id=c1) is True
    # Second tab: same user, must NOT re-announce.
    assert await p.join(room_id=room, user_id=user, connection_id=c2) is False
    assert await p.list_room(room) == [user]

    # Closing the first tab while the second is live must NOT drop the user.
    assert await p.leave(room_id=room, user_id=user, connection_id=c1) is False
    assert await p.list_room(room) == [user]

    # Closing the last tab fully removes the user.
    assert await p.leave(room_id=room, user_id=user, connection_id=c2) is True
    assert await p.list_room(room) == []


@pytest.mark.asyncio
async def test_scrub_removes_user_without_conns(fake_redis: _FakeRedis) -> None:
    p = PresenceTracker()
    room = uuid.uuid4()
    user = uuid.uuid4()
    await p.join(room_id=room, user_id=user, connection_id=uuid.uuid4())

    # Simulate the conns SET expiring (connection died without clean leave).
    fake_redis.sets.pop(f"ws:presence:{room}:{user}:conns", None)

    removed = await presence_mod.scrub_stale_presence()
    assert removed == 1
    assert await p.list_room(room) == []
    assert f"ws:user:{user}:rooms" not in fake_redis.sets


@pytest.mark.asyncio
async def test_scrub_keeps_live_user(fake_redis: _FakeRedis) -> None:
    p = PresenceTracker()
    room = uuid.uuid4()
    user = uuid.uuid4()
    await p.join(room_id=room, user_id=user, connection_id=uuid.uuid4())

    removed = await presence_mod.scrub_stale_presence()
    assert removed == 0
    assert await p.list_room(room) == [user]
