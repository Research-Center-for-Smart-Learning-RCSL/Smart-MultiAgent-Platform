"""`DraftStore` key layout, caps, ages and reconciliation (§32, [R32.02]).

What this file can and cannot establish is worth stating, because the split decides
where each acceptance criterion is actually verified.

**Here (fake Redis):** the key shape, that nothing escapes the `ws:draft:` prefix, the
per-surface and per-user caps, that an empty update clears rather than stores, that a
returned entry carries its age, and that an index member whose entry is gone is
reconciled away rather than returned. All of these are decisions this module makes,
so a fake is the honest instrument.

**Not here (`tests/integration/test_draft_store_ttl.py`, `pytest.mark.db`):** that the
TTL actually expires and that the expiry reached the server. A
fake clock proves nothing about `EXPIRE` — it proves the fake, and this is the one
property whose failure mode is unsent text outliving the window it was promised.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Set as AbstractSet
from datetime import timedelta
from typing import Any

import pytest

from contexts.conversation.infrastructure import drafts as drafts_mod
from contexts.conversation.infrastructure.drafts import (
    ACTIVITY,
    COMPOSER,
    MAX_CONTENT_CHARS,
    MAX_USER_CHARS,
    MAX_USER_ENTRIES,
    TRUNCATION_MARKER,
    DraftStore,
    normalise_key,
)


class _FakeRedis:
    """In-memory subset of the string+SET surface `drafts.py` uses.

    Hand-rolled rather than `fakeredis`, matching `test_presence.py`: the surface is
    six commands, and a fake this small is read in full by a reviewer.

    It does **not** model expiry. That is deliberate — see the module docstring.
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.expires: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        if ex is not None:
            self.expires[key] = ex
        return True

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.values.get(k) for k in keys]

    async def sadd(self, key: str, member: str) -> int:
        s = self.sets.setdefault(key, set())
        added = member not in s
        s.add(member)
        return int(added)

    async def srem(self, key: str, member: str) -> int:
        s = self.sets.get(key)
        if not s or member not in s:
            return 0
        s.discard(member)
        return 1

    # `AbstractSet` rather than `set`: this class defines a method named `set` to
    # mimic Redis, and mypy resolves a bare `set[str]` annotation against the class
    # scope, where that method shadows the builtin.
    async def smembers(self, key: str) -> AbstractSet[str]:
        return set(self.sets.get(key, set()))

    async def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        self.sets.pop(key, None)
        return int(existed)

    async def expire(self, key: str, ttl: int) -> bool:
        self.expires[key] = ttl
        return True

    def pipeline(self, transaction: bool = False) -> _FakePipe:
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[Any, ...]] = []

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._ops.append(("set", key, value, ex))

    def sadd(self, key: str, member: str) -> None:
        self._ops.append(("sadd", key, member))

    def srem(self, key: str, member: str) -> None:
        self._ops.append(("srem", key, member))

    def delete(self, key: str) -> None:
        self._ops.append(("delete", key))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", key, ttl))

    async def execute(self) -> list[Any]:
        out: list[Any] = []
        for op in self._ops:
            name = op[0]
            if name == "set":
                out.append(await self._redis.set(op[1], op[2], ex=op[3]))
            elif name == "sadd":
                out.append(await self._redis.sadd(op[1], op[2]))
            elif name == "srem":
                out.append(await self._redis.srem(op[1], op[2]))
            elif name == "delete":
                out.append(await self._redis.delete(op[1]))
            elif name == "expire":
                out.append(await self._redis.expire(op[1], op[2]))
        self._ops = []
        return out


ROOM = uuid.UUID("11111111-1111-4111-8111-111111111111")
ALICE = uuid.UUID("22222222-2222-4222-8222-222222222222")
BOB = uuid.UUID("33333333-3333-4333-8333-333333333333")


@pytest.fixture
def redis() -> _FakeRedis:
    return _FakeRedis()


@pytest.fixture
def store(redis: _FakeRedis) -> DraftStore:
    return DraftStore(redis)


class TestKeyLayout:
    async def test_a_composer_draft_lands_under_the_draft_prefix(
        self, store: DraftStore, redis: _FakeRedis
    ) -> None:
        await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="half a thought")

        assert list(redis.values) == [f"ws:draft:{ROOM}:{ALICE}:composer"]

    async def test_an_activity_draft_carries_its_type_key(self, store: DraftStore, redis: _FakeRedis) -> None:
        await store.put(room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key="mandala-9grid", content="{}")

        assert list(redis.values) == [f"ws:draft:{ROOM}:{ALICE}:activity:mandala-9grid"]

    def test_no_draft_key_sits_under_the_presence_prefix(self) -> None:
        """`scrub_stale_presence` scans `ws:presence:*` and discriminates by counting
        ':'. A draft key underneath it would be read as a roster or conns key by a
        sweep that predates this module — the trap `_typing_key` already records."""
        composer = drafts_mod._entry_key(ROOM, ALICE, COMPOSER, None)
        activity = drafts_mod._entry_key(ROOM, ALICE, ACTIVITY, "k")
        index = drafts_mod._index_key(ROOM)

        for key in (composer, activity, index):
            assert not key.startswith("ws:presence:"), key
            assert key.startswith("ws:draft:"), key

    async def test_the_room_index_holds_every_entry_key(self, store: DraftStore, redis: _FakeRedis) -> None:
        await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="a")
        await store.put(room_id=ROOM, user_id=BOB, surface=ACTIVITY, key="k", content="b")

        assert redis.sets[f"ws:draft:rooms:{ROOM}"] == set(redis.values)


class TestMalformedFramesAreDropped:
    @pytest.mark.parametrize("surface", ["", "message", "COMPOSER", "presence"])
    async def test_an_unknown_surface_writes_nothing(
        self, store: DraftStore, redis: _FakeRedis, surface: str
    ) -> None:
        assert await store.put(room_id=ROOM, user_id=ALICE, surface=surface, key=None, content="x") is False
        assert not redis.values

    async def test_an_activity_draft_without_a_key_writes_nothing(
        self, store: DraftStore, redis: _FakeRedis
    ) -> None:
        """Storing it would create an entry `draft.clear` could never name, so it
        would sit until its TTL with no way for its author to retract it."""
        assert await store.put(room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key=None, content="x") is False
        assert not redis.values

    @pytest.mark.parametrize(
        "raw",
        ["a:b", "a b", "a*", "a?", "a\nb", "a\tb", "", "   ", "x" * 129, None],
    )
    def test_a_key_that_could_reshape_or_escape_its_own_key_is_refused(self, raw: Any) -> None:
        """':' would move the entry into another surface's key shape; the rest would
        make the stored key unmatchable by the client that wrote it. A real
        `ActivityType.key` contains none of them."""
        assert normalise_key(raw) is None

    async def test_a_rejected_key_is_not_written_through_put(
        self, store: DraftStore, redis: _FakeRedis
    ) -> None:
        """`put` re-checks rather than trusting its caller: the WS handler normalises,
        and a second caller added later must not be able to skip it."""
        assert await store.put(room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key="a:b", content="x") is False
        assert not redis.values


class TestEmptyContentClears:
    async def test_an_emptied_composer_draft_is_deleted_not_stored(
        self, store: DraftStore, redis: _FakeRedis
    ) -> None:
        """Select-all-and-delete is a retraction. An empty string left in Redis would
        be returned to an agent as "they are composing" for the next fifteen minutes."""
        await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="something")

        assert await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="") is False

        assert not redis.values
        assert not redis.sets[f"ws:draft:rooms:{ROOM}"]

    async def test_whitespace_only_counts_as_empty(self, store: DraftStore, redis: _FakeRedis) -> None:
        await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="   \n\t ")

        assert not redis.values


class TestCaps:
    async def test_a_long_draft_is_truncated_with_a_marker_never_dropped(self, store: DraftStore) -> None:
        """A participant who writes past the cap has written something, and storing
        nothing would make a long answer indistinguishable from an empty one."""
        await store.put(
            room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="x" * (MAX_CONTENT_CHARS + 500)
        )

        entries = await store.list_for_room(ROOM)

        assert len(entries) == 1
        assert entries[0].truncated is True
        assert entries[0].content.endswith(TRUNCATION_MARKER)
        assert len(entries[0].content) == MAX_CONTENT_CHARS + len(TRUNCATION_MARKER)

    async def test_the_per_user_budget_refuses_the_new_value(self, store: DraftStore) -> None:
        near_cap = "x" * MAX_CONTENT_CHARS
        for index in range(MAX_USER_CHARS // MAX_CONTENT_CHARS):
            assert await store.put(
                room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key=f"t{index}", content=near_cap
            )

        assert (
            await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content=near_cap)
            is False
        )

    async def test_the_budget_never_evicts_another_surface(self, store: DraftStore) -> None:
        """Evicting would let a participant's chat draft silently delete their
        worksheet draft — a data loss they cannot see and did not cause."""
        near_cap = "x" * MAX_CONTENT_CHARS
        for index in range(MAX_USER_CHARS // MAX_CONTENT_CHARS):
            await store.put(room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key=f"t{index}", content=near_cap)

        await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content=near_cap)

        surfaces = {(e.surface, e.key) for e in await store.list_for_room(ROOM)}
        assert surfaces == {("activity", f"t{i}") for i in range(MAX_USER_CHARS // MAX_CONTENT_CHARS)}

    async def test_a_participant_cannot_mint_unbounded_entries(self, store: DraftStore) -> None:
        """Security gate finding, Introduced/MEDIUM. The regression for it.

        The byte budget does not bound entry *count*: a thousand one-character
        drafts under a thousand distinct activity keys sit well inside it. Each is
        its own Redis key and its own index member, so without a count cap a
        hostile client mints entries at the throttle rate for a whole TTL window
        and makes `list_for_room`'s MGET enormous on every agent turn.
        """
        for index in range(MAX_USER_ENTRIES):
            assert await store.put(
                room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key=f"t{index}", content="x"
            )

        assert (
            await store.put(room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key="one-more", content="x")
            is False
        )
        assert len(await store.list_for_room(ROOM)) == MAX_USER_ENTRIES

    async def test_an_expired_entry_does_not_count_toward_the_cap(
        self, store: DraftStore, redis: _FakeRedis
    ) -> None:
        """`/code-review` finding. The index outlives its entries on purpose and is
        reconciled only on the read path, so counting raw `SMEMBERS` would treat a
        lapsed member as a live entry.

        Closing a tab does not fire the client's unmount hook, so these accumulate in
        ordinary use — and once eight had, the participant's own chat draft would be
        refused, silently and permanently.
        """
        for index in range(MAX_USER_ENTRIES):
            await store.put(room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key=f"t{index}", content="x")
        # Every entry lapses; the index members survive, as they really do.
        for key in list(redis.values):
            del redis.values[key]

        assert await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="mine")

    async def test_a_lapsed_member_is_pruned_from_the_index(
        self, store: DraftStore, redis: _FakeRedis
    ) -> None:
        """Self-healing, not merely tolerated: the prune is what keeps the set from
        growing without bound in a room where no agent ever calls the tool."""
        await store.put(room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key="gone", content="x")
        del redis.values[f"ws:draft:{ROOM}:{ALICE}:activity:gone"]

        await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="mine")

        assert redis.sets[f"ws:draft:rooms:{ROOM}"] == {f"ws:draft:{ROOM}:{ALICE}:composer"}

    async def test_the_count_cap_never_blocks_editing_an_existing_draft(self, store: DraftStore) -> None:
        """Measured against the participant's *other* entries, so a student sitting
        at the cap can still edit the worksheet in front of them."""
        for index in range(MAX_USER_ENTRIES):
            await store.put(room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key=f"t{index}", content="x")

        assert await store.put(room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key="t0", content="edited")

    async def test_the_count_cap_is_per_user(self, store: DraftStore) -> None:
        for index in range(MAX_USER_ENTRIES):
            await store.put(room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key=f"t{index}", content="x")

        assert await store.put(room_id=ROOM, user_id=BOB, surface=COMPOSER, key=None, content="mine")

    async def test_the_budget_is_per_user_not_per_room(self, store: DraftStore) -> None:
        """One participant filling their own budget must not stop the rest of the
        class from being seen at all."""
        near_cap = "x" * MAX_CONTENT_CHARS
        for index in range(MAX_USER_CHARS // MAX_CONTENT_CHARS):
            await store.put(room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key=f"t{index}", content=near_cap)

        assert await store.put(room_id=ROOM, user_id=BOB, surface=COMPOSER, key=None, content="mine")

    async def test_replacing_a_draft_at_the_budget_is_still_possible(self, store: DraftStore) -> None:
        """Measured against the participant's *other* surfaces, so a student whose
        worksheet already fills the budget can still edit that worksheet."""
        await store.put(
            room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key="t", content="x" * MAX_CONTENT_CHARS
        )

        assert await store.put(
            room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key="t", content="y" * MAX_CONTENT_CHARS
        )


class TestReadingBack:
    async def test_every_entry_carries_its_age(self, store: DraftStore, redis: _FakeRedis) -> None:
        """[R32.04]. A draft survives a disconnect for up to its TTL, so without the
        age an agent cannot tell live typing from a tab closed twelve minutes ago."""
        await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="fresh")
        key = f"ws:draft:{ROOM}:{ALICE}:composer"
        stored = json.loads(redis.values[key])
        stored["updated_at"] = (drafts_mod.now() - timedelta(seconds=360)).isoformat()
        redis.values[key] = json.dumps(stored)

        entries = await store.list_for_room(ROOM)

        assert entries[0].age_seconds == pytest.approx(360, abs=2)

    async def test_entries_come_back_newest_first(self, store: DraftStore, redis: _FakeRedis) -> None:
        await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="old")
        await store.put(room_id=ROOM, user_id=BOB, surface=COMPOSER, key=None, content="new")
        old_key = f"ws:draft:{ROOM}:{ALICE}:composer"
        stored = json.loads(redis.values[old_key])
        stored["updated_at"] = (drafts_mod.now() - timedelta(seconds=300)).isoformat()
        redis.values[old_key] = json.dumps(stored)

        entries = await store.list_for_room(ROOM)

        assert [e.user_id for e in entries] == [BOB, ALICE]

    async def test_a_cleared_draft_is_gone_from_both_the_value_and_the_index(
        self, store: DraftStore, redis: _FakeRedis
    ) -> None:
        await store.put(room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key="k", content="partial")

        await store.clear(room_id=ROOM, user_id=ALICE, surface=ACTIVITY, key="k")

        assert not redis.values
        assert not redis.sets[f"ws:draft:rooms:{ROOM}"]
        assert await store.list_for_room(ROOM) == []

    async def test_an_evicted_entry_reads_as_absent_and_is_reconciled_out(
        self, store: DraftStore, redis: _FakeRedis
    ) -> None:
        """Redis runs `allkeys-lru`, so an entry can vanish while its index member
        remains. The result must be "no draft", never stale-but-wrong data — which is
        what makes eviction a bounded loss. Mirrors `presence._reconcile_roster`."""
        await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="evicted")
        await store.put(room_id=ROOM, user_id=BOB, surface=COMPOSER, key=None, content="kept")
        del redis.values[f"ws:draft:{ROOM}:{ALICE}:composer"]

        entries = await store.list_for_room(ROOM)

        assert [e.user_id for e in entries] == [BOB]
        assert redis.sets[f"ws:draft:rooms:{ROOM}"] == {f"ws:draft:{ROOM}:{BOB}:composer"}

    async def test_a_foreign_or_malformed_index_member_is_dropped_not_attributed(
        self, store: DraftStore, redis: _FakeRedis
    ) -> None:
        """A key from another room or an older format could survive a deploy inside
        the index, and a mis-attributed draft is worse than a dropped one."""
        await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="mine")
        other = uuid.uuid4()
        redis.sets[f"ws:draft:rooms:{ROOM}"].add(f"ws:draft:{other}:{BOB}:composer")
        redis.values[f"ws:draft:{other}:{BOB}:composer"] = json.dumps({"content": "theirs"})
        redis.sets[f"ws:draft:rooms:{ROOM}"].add(f"ws:draft:{ROOM}:not-a-uuid:composer")
        redis.values[f"ws:draft:{ROOM}:not-a-uuid:composer"] = json.dumps({"content": "junk"})

        entries = await store.list_for_room(ROOM)

        assert [(e.user_id, e.content) for e in entries] == [(ALICE, "mine")]

    async def test_an_unparseable_payload_yields_no_entry(self, store: DraftStore, redis: _FakeRedis) -> None:
        await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="ok")
        redis.values[f"ws:draft:{ROOM}:{ALICE}:composer"] = "{not json"

        assert await store.list_for_room(ROOM) == []

    async def test_an_empty_room_reads_empty(self, store: DraftStore) -> None:
        assert await store.list_for_room(uuid.uuid4()) == []

    async def test_clear_room_removes_the_whole_footprint(self, store: DraftStore, redis: _FakeRedis) -> None:
        """§8's claim that deleting the room's Redis keys deletes everything this
        feature ever stored, as an operation rather than an assertion."""
        await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="a")
        await store.put(room_id=ROOM, user_id=BOB, surface=ACTIVITY, key="k", content="b")

        await store.clear_room(ROOM)

        assert not redis.values
        assert not redis.sets


class TestFailureIsSilentAndTotal:
    """A Redis fault costs a draft, never a socket or a turn."""

    class _Broken:
        def pipeline(self, transaction: bool = False) -> Any:
            raise RuntimeError("redis is down")

        async def smembers(self, key: str) -> set[str]:
            raise RuntimeError("redis is down")

    async def test_a_write_failure_returns_false(self) -> None:
        store = DraftStore(self._Broken())  # type: ignore[arg-type]

        assert await store.put(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None, content="x") is False

    async def test_a_read_failure_returns_no_drafts(self) -> None:
        store = DraftStore(self._Broken())  # type: ignore[arg-type]

        assert await store.list_for_room(ROOM) == []

    async def test_a_clear_failure_does_not_raise(self) -> None:
        store = DraftStore(self._Broken())  # type: ignore[arg-type]

        await store.clear(room_id=ROOM, user_id=ALICE, surface=COMPOSER, key=None)
