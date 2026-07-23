"""F-5 / C3: xclaim_refresh resets an in-flight entry's PEL idle clock.

Without a liveness input the reclaim (xautoclaim_stale) treats time-since-
delivery as time-since-owner-alive and steals an actively-processed entry.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

import contexts.orchestration.infrastructure.a2a_streams as streams


@pytest.mark.asyncio
async def test_xclaim_refresh_issues_justid_for_inflight_ids(monkeypatch) -> None:
    fake = AsyncMock()
    monkeypatch.setattr(streams, "get_redis", lambda: fake)
    agent_id = uuid.uuid4()
    ids = ["1-0", "2-0"]

    await streams.xclaim_refresh(agent_id, ids)

    fake.xclaim.assert_awaited_once()
    args, kwargs = fake.xclaim.call_args
    assert args[0] == streams._inbox_key(agent_id)
    assert args[1] == streams._CONSUMER_GROUP  # "agent-runtime"
    assert args[2] == streams._consumer_name(agent_id)  # this process's consumer
    assert args[3] == 0  # min_idle_time=0 — claim unconditionally, we own them
    assert args[4] == ids
    assert kwargs["justid"] is True  # resets idle time without bumping retry count


@pytest.mark.asyncio
async def test_xclaim_refresh_noop_on_empty(monkeypatch) -> None:
    fake = AsyncMock()
    monkeypatch.setattr(streams, "get_redis", lambda: fake)

    await streams.xclaim_refresh(uuid.uuid4(), [])

    fake.xclaim.assert_not_awaited()
