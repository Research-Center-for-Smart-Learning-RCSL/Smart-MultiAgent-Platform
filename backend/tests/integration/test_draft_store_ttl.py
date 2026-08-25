"""`DraftStore` against a real Redis: the TTL, and that nothing outlives it (§32).

WHY THIS CANNOT BE A UNIT TEST
------------------------------
`tests/unit/test_draft_store.py` covers the key layout, the caps, the ages and the
index reconciliation against a hand-rolled fake — every decision the module itself
makes. It deliberately does **not** model expiry, because a fake clock over a fake
Redis proves the fake: `SET ... EX` either reached the server or it did not, and the
unit tier cannot tell those apart.

That distinction matters more here than it usually would. The TTL is the *only* thing
bounding how long a person's unsent words remain readable after they close the tab
([R32.02], OQ-1). A `put` that silently stored without an expiry would pass all 38
unit tests and leave half-typed accounts of distressing events in Redis until an
operator flushed the database by hand.

So this file asserts the expiry the server actually recorded, rather than sleeping
through a 900-second window: `TTL` is a server fact, and reading it back proves the
command carried `EX`. One short-lived key is expired for real, to prove the read path
treats a lapsed entry as absent rather than as an error.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from contexts.conversation.infrastructure.drafts import (
    ACTIVITY,
    COMPOSER,
    DRAFT_TTL_SECONDS,
    DraftStore,
)

pytestmark = pytest.mark.db


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    """A real client on the configured Redis, decoded like the app's own.

    Built here rather than through `get_redis()` so the module-global singleton is
    not left holding a connection an event loop from another test has closed — the
    same reason the sessionmaker fixture builds its own engine.
    """
    from app.config.settings import get_settings

    client: Redis = Redis.from_url(get_settings().redis.dsn, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


class TestTheExpiryIsRealAndServerSide:
    async def test_a_stored_draft_carries_the_declared_ttl(self, redis: Redis) -> None:
        room, user = uuid.uuid4(), uuid.uuid4()
        store = DraftStore(redis)
        try:
            await store.put(room_id=room, user_id=user, surface=COMPOSER, key=None, content="unsent")

            ttl = await redis.ttl(f"ws:draft:{room}:{user}:composer")

            # -1 is "no expiry" and -2 is "no key". Both are the failure this test
            # exists for, and neither is distinguishable from success in the fake.
            assert ttl > 0, f"draft key carries no expiry (TTL {ttl})"
            assert ttl <= DRAFT_TTL_SECONDS
            assert ttl > DRAFT_TTL_SECONDS - 60
        finally:
            await store.clear_room(room)

    async def test_an_activity_draft_carries_it_too(self, redis: Redis) -> None:
        """The two surfaces take different key shapes, and a per-surface `EX` is
        exactly the kind of thing that gets added on one branch and missed on the
        other."""
        room, user = uuid.uuid4(), uuid.uuid4()
        store = DraftStore(redis)
        try:
            await store.put(room_id=room, user_id=user, surface=ACTIVITY, key="mandala-9grid", content="{}")

            ttl = await redis.ttl(f"ws:draft:{room}:{user}:activity:mandala-9grid")

            assert ttl > 0, f"activity draft key carries no expiry (TTL {ttl})"
        finally:
            await store.clear_room(room)

    async def test_an_update_refreshes_the_ttl(self, redis: Redis) -> None:
        """[R32.02] — "refreshed by each update". Without it a student who types for
        sixteen minutes has their draft expire mid-sentence."""
        room, user = uuid.uuid4(), uuid.uuid4()
        store = DraftStore(redis)
        key = f"ws:draft:{room}:{user}:composer"
        try:
            await store.put(room_id=room, user_id=user, surface=COMPOSER, key=None, content="first")
            await redis.expire(key, 10)

            await store.put(room_id=room, user_id=user, surface=COMPOSER, key=None, content="second")

            assert await redis.ttl(key) > 60
        finally:
            await store.clear_room(room)

    async def test_the_room_index_outlives_a_single_entry(self, redis: Redis) -> None:
        """An entry that expires between an `SMEMBERS` and its `MGET` must leave a
        reconcilable member behind, not an index that vanished under the read."""
        room, user = uuid.uuid4(), uuid.uuid4()
        store = DraftStore(redis)
        try:
            await store.put(room_id=room, user_id=user, surface=COMPOSER, key=None, content="unsent")

            entry_ttl = await redis.ttl(f"ws:draft:{room}:{user}:composer")
            index_ttl = await redis.ttl(f"ws:draft:rooms:{room}")

            assert index_ttl > entry_ttl
        finally:
            await store.clear_room(room)

    async def test_a_lapsed_entry_reads_as_absent_and_leaves_no_member(self, redis: Redis) -> None:
        """The expiry actually taking effect, not merely being recorded.

        The key's TTL is cut to one second and waited out, which is the shortest
        honest form of "the window closed". A lapsed draft must read as no draft —
        never as an error on an agent's turn, and never as stale content.
        """
        room, alice, bob = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        store = DraftStore(redis)
        try:
            await store.put(room_id=room, user_id=alice, surface=COMPOSER, key=None, content="lapses")
            await store.put(room_id=room, user_id=bob, surface=COMPOSER, key=None, content="stays")
            await redis.expire(f"ws:draft:{room}:{alice}:composer", 1)
            await asyncio.sleep(1.5)

            entries = await store.list_for_room(room)

            assert [e.user_id for e in entries] == [bob]
            assert await redis.smembers(f"ws:draft:rooms:{room}") == {f"ws:draft:{room}:{bob}:composer"}
        finally:
            await store.clear_room(room)

    async def test_clear_room_leaves_nothing_behind(self, redis: Redis) -> None:
        """§8's claim about the feature's entire data footprint, against a real
        keyspace rather than a dict."""
        room, user = uuid.uuid4(), uuid.uuid4()
        store = DraftStore(redis)
        await store.put(room_id=room, user_id=user, surface=COMPOSER, key=None, content="a")
        await store.put(room_id=room, user_id=user, surface=ACTIVITY, key="k", content="b")

        await store.clear_room(room)

        assert [key async for key in redis.scan_iter(match=f"ws:draft:*{room}*", count=100)] == []
