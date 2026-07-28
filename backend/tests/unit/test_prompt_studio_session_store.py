"""Regression test for SessionStore.append_message concurrency (§29).

append_message must re-fetch the session from Redis before appending, not
trust a caller-held snapshot — otherwise a worker that read the session
before streaming a reply clobbers a message the user posted mid-stream.
"""

from __future__ import annotations

import uuid

import pytest

from contexts.prompt_studio.domain.models import SessionMessage
from contexts.prompt_studio.infrastructure.session_store import SessionStore, _session_key


class _FakeRedis:
    """In-memory subset of the Redis GET/SET/pipeline surface SessionStore uses."""

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value.encode() if isinstance(value, str) else value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    def pipeline(self, transaction: bool = False) -> _FakePipe:
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list = []

    def incr(self, key: str) -> None:
        self._ops.append(("incr", key))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", key, ttl))

    async def execute(self) -> list:
        results = []
        for op in self._ops:
            if op[0] == "incr":
                current = int(self._redis.values.get(op[1], b"0"))
                current += 1
                self._redis.values[op[1]] = str(current).encode()
                results.append(current)
            elif op[0] == "expire":
                results.append(True)
        return results


@pytest.mark.asyncio
async def test_append_message_does_not_clobber_concurrent_append() -> None:
    """A stale snapshot must not overwrite a message appended after it was read."""
    redis = _FakeRedis()
    store = SessionStore(redis)
    user_id, project_id = uuid.uuid4(), uuid.uuid4()
    session = await store.create(user_id=user_id, project_id=project_id)

    # Simulate a caller (e.g. the worker) holding an early snapshot with zero
    # messages, then a concurrent append happening before the worker acts.
    stale_snapshot = session
    assert stale_snapshot.messages == ()

    await store.append_message(session.session_id, SessionMessage(role="user", content="u2"))

    # The worker appends its reply using the session id, not the stale snapshot.
    updated = await store.append_message(
        session.session_id, SessionMessage(role="assistant", content="reply")
    )

    assert updated is not None
    assert [m.content for m in updated.messages] == ["u2", "reply"]

    fetched = await store.get(session.session_id)
    assert fetched is not None
    assert [m.content for m in fetched.messages] == ["u2", "reply"]


@pytest.mark.asyncio
async def test_append_message_returns_none_for_expired_session() -> None:
    redis = _FakeRedis()
    store = SessionStore(redis)
    missing_id = uuid.uuid4()

    result = await store.append_message(missing_id, SessionMessage(role="user", content="hi"))

    assert result is None


@pytest.mark.asyncio
async def test_error_flag_round_trips_through_json() -> None:
    """Q-5's failure marker: the `error` flag must survive a real JSON save/load, not just the dataclass default."""
    redis = _FakeRedis()
    store = SessionStore(redis)
    user_id, project_id = uuid.uuid4(), uuid.uuid4()
    session = await store.create(user_id=user_id, project_id=project_id)

    await store.append_message(
        session.session_id,
        SessionMessage(role="assistant", content="prompt-studio/turn-failed", error=True),
    )

    fetched = await store.get(session.session_id)

    assert fetched is not None
    assert [(m.content, m.error) for m in fetched.messages] == [("prompt-studio/turn-failed", True)]


@pytest.mark.asyncio
async def test_get_tolerates_pre_existing_json_without_the_error_key() -> None:
    """Back-compat: sessions saved before this fix have no "error" key at all."""
    redis = _FakeRedis()
    session_id = uuid.uuid4()
    user_id, project_id = uuid.uuid4(), uuid.uuid4()
    await redis.set(
        _session_key(session_id),
        f'{{"user_id": "{user_id}", "project_id": "{project_id}", '
        f'"messages": [{{"role": "assistant", "content": "old reply"}}]}}',
    )
    store = SessionStore(redis)

    fetched = await store.get(session_id)

    assert fetched is not None
    assert fetched.messages == (SessionMessage(role="assistant", content="old reply", error=False),)
