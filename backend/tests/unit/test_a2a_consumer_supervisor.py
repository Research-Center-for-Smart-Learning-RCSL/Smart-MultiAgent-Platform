"""F-20: the A2A consumer supervisor must stop loops for deleted agents.

Discovery from Redis is create-only and self-referential — run_consumer_loop's
ensure_consumer_group recreates the a2a:agent:{id} key via mkstream — so the
supervisor needs a DB-backed liveness filter to know which loops to tear down.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

import contexts.orchestration.application.a2a_consumer as consumer


class _FakeRedis:
    """Only what _discover_agents needs: an async scan_iter over a key set that
    the test controls (and can keep returning a deleted agent's key, modelling
    mkstream recreating it)."""

    def __init__(self, keys: set[str]) -> None:
        self.keys = keys

    async def scan_iter(self, match: str | None = None, count: int | None = None):
        for k in list(self.keys):
            yield k


async def _handler(_env) -> None:  # pragma: no cover - never invoked in these tests
    pass


async def _stub_loop(agent_id, handler, *, shutdown_event=None, on_dlq=None) -> None:
    """Stand-in for run_consumer_loop: a real, cancellable task that never exits
    on its own, so the supervisor's create/cancel bookkeeping is what's tested."""
    await asyncio.Event().wait()


def _key(agent_id: uuid.UUID) -> str:
    return f"a2a:agent:{agent_id}"


@pytest.fixture
def patched(monkeypatch):
    def _make(keys: set[str]):
        fake = _FakeRedis(keys)
        monkeypatch.setattr(consumer, "get_redis", lambda: fake)
        monkeypatch.setattr(consumer, "run_consumer_loop", _stub_loop)
        return fake

    return _make


@pytest.mark.asyncio
async def test_reconcile_stops_loop_for_deleted_agent(patched) -> None:
    a1, a2 = uuid.uuid4(), uuid.uuid4()
    patched({_key(a1), _key(a2)})
    live = {a1, a2}

    async def liveness(ids):
        return ids & live

    sup = consumer.A2AConsumerSupervisor(_handler, liveness=liveness)
    await sup._reconcile()
    assert set(sup._loops) == {a1, a2}
    task_a2 = sup._loops[a2]

    live = {a1}  # a2 soft-deleted
    await sup._reconcile()

    assert set(sup._loops) == {a1}
    assert task_a2.cancelled()
    await sup._stop_all()


@pytest.mark.asyncio
async def test_deleted_agent_loop_is_not_recreated_by_stream_key(patched) -> None:
    a1, a2 = uuid.uuid4(), uuid.uuid4()
    # SCAN keeps returning a2's key across every round — mkstream recreates it.
    patched({_key(a1), _key(a2)})
    live = {a1}

    async def liveness(ids):
        return ids & live

    sup = consumer.A2AConsumerSupervisor(_handler, liveness=liveness)
    for _ in range(3):
        await sup._reconcile()

    assert set(sup._loops) == {a1}  # a2 never (re)created despite its live key
    await sup._stop_all()


@pytest.mark.asyncio
async def test_restored_agent_loop_is_recreated(patched) -> None:
    a1, a2 = uuid.uuid4(), uuid.uuid4()
    patched({_key(a1), _key(a2)})
    live = {a1}

    async def liveness(ids):
        return ids & live

    sup = consumer.A2AConsumerSupervisor(_handler, liveness=liveness)
    await sup._reconcile()
    assert set(sup._loops) == {a1}

    live = {a1, a2}  # a2 restored (admin_restore clears deleted_at)
    await sup._reconcile()

    assert set(sup._loops) == {a1, a2}
    await sup._stop_all()


@pytest.mark.asyncio
async def test_liveness_error_keeps_all_loops(patched) -> None:
    a1, a2 = uuid.uuid4(), uuid.uuid4()
    patched({_key(a1), _key(a2)})
    state = {"raise": False}

    async def liveness(ids):
        if state["raise"]:
            raise RuntimeError("db down")
        return ids

    sup = consumer.A2AConsumerSupervisor(_handler, liveness=liveness)
    await sup._reconcile()
    task_a1, task_a2 = sup._loops[a1], sup._loops[a2]

    state["raise"] = True  # transient DB failure on the next scan
    await sup._reconcile()

    assert set(sup._loops) == {a1, a2}
    assert not task_a1.cancelled()
    assert not task_a2.cancelled()
    await sup._stop_all()


@pytest.mark.asyncio
async def test_stop_all_still_clears_loops(patched) -> None:
    a1, a2 = uuid.uuid4(), uuid.uuid4()
    patched({_key(a1), _key(a2)})

    async def liveness(ids):
        return ids

    sup = consumer.A2AConsumerSupervisor(_handler, liveness=liveness)
    await sup._reconcile()
    tasks = list(sup._loops.values())

    await sup._stop_all()

    assert sup._loops == {}
    assert all(t.cancelled() for t in tasks)


@pytest.mark.asyncio
async def test_no_liveness_filter_is_create_only(patched) -> None:
    # Backward-compat: without a liveness filter the supervisor never prunes,
    # exactly as before C4 (F-20). A vanished SCAN key just stops re-creating.
    a1 = uuid.uuid4()
    patched({_key(a1)})

    sup = consumer.A2AConsumerSupervisor(_handler)
    await sup._reconcile()
    assert set(sup._loops) == {a1}
    await sup._stop_all()
