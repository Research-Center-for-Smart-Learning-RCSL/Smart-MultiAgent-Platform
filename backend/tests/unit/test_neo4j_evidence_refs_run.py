"""Failure paths of the ``neo4j-evidence-refs`` backfill, with fake stores.

The happy path and the busy skip run against real Neo4j and Redis in
``tests/integration/test_neo4j_evidence_refs_expand.py``. These cover what a
real stack cannot stage on demand: a lock lost mid-copy, one config's copy
raising, and edges whose config id cannot be attributed.
"""

from __future__ import annotations

import uuid

import pytest

from smap.maintenance import neo4j_evidence_refs as cmd


class _Driver:
    def __init__(self, pending: dict[str, int], *, fail_for: uuid.UUID | None = None) -> None:
        self.pending = dict(pending)
        self.fail_for = fail_for

    async def count_unmigrated_evidence(self) -> dict[str, int]:
        return {k: v for k, v in self.pending.items() if v}

    async def copy_evidence_refs(self, *, config_id: uuid.UUID, batch: int) -> int:
        if config_id == self.fail_for:
            raise RuntimeError("neo4j transaction timed out")
        key = str(config_id)
        n = min(batch, self.pending.get(key, 0))
        self.pending[key] = self.pending.get(key, 0) - n
        return n


class _Locks:
    def __init__(self, *, lose_after: int | None = None) -> None:
        self.refreshes = 0
        self.lose_after = lose_after
        self.released: list[uuid.UUID] = []

    async def acquire(self, config_id: uuid.UUID, *, ttl_s: int) -> bool:
        return True

    async def refresh(self, config_id: uuid.UUID, *, ttl_s: int) -> bool:
        self.refreshes += 1
        return self.lose_after is None or self.refreshes <= self.lose_after

    async def release(self, config_id: uuid.UUID) -> None:
        self.released.append(config_id)

    async def force_release(self, config_id: uuid.UUID) -> None:
        return None


@pytest.fixture(autouse=True)
def armed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cmd.ARMED_ENV, "1")


@pytest.mark.asyncio
async def test_a_lock_lost_mid_copy_is_reported_apart_from_busy() -> None:
    cid = uuid.uuid4()
    locks = _Locks(lose_after=0)
    report = await cmd.run(driver=_Driver({str(cid): 5}), lock_store=locks, batch=2)
    assert report.lost_lock == (cid,)
    assert report.busy == ()
    assert (report.copied, report.remaining) == (2, 3)
    assert locks.released == [cid]


@pytest.mark.asyncio
async def test_one_config_failing_does_not_stop_the_others() -> None:
    bad, good = sorted((uuid.uuid4(), uuid.uuid4()), key=str)
    locks = _Locks()
    driver = _Driver({str(bad): 1, str(good): 2}, fail_for=bad)
    report = await cmd.run(driver=driver, lock_store=locks)
    assert report.failed == (bad,)
    assert (report.copied, report.remaining) == (2, 1)
    # The failed config's lock was still released.
    assert set(locks.released) == {bad, good}


@pytest.mark.asyncio
async def test_unattributed_edges_count_as_remaining() -> None:
    cid = uuid.uuid4()
    report = await cmd.run(driver=_Driver({str(cid): 1, "": 2, "not-a-uuid": 1}), lock_store=_Locks())
    assert report.unattributed == 3
    assert (report.copied, report.remaining) == (1, 3)


@pytest.mark.asyncio
async def test_a_dry_run_counts_unattributed_edges_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cmd.ARMED_ENV)
    report = await cmd.run(driver=_Driver({"": 2}), lock_store=_Locks())
    assert (report.dry_run, report.pending, report.remaining, report.unattributed) == (True, 2, 2, 2)
