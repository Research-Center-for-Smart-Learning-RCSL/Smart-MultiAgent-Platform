"""Unit tests for the admin reset path (R11a.02 / F-26).

Verifies :meth:`GraphRagConfigService.admin_reset` compensates the two-phase
external state — acquiring the build lock, discarding any in-flight build
(Neo4j rollback + snapshot/pointer cleanup) — before forcing ``idle``, and that
``force`` governs both lock contention and compensation failure honestly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from contexts.knowledge.application.graphrag_config_service import (
    GraphRagConfigService,
)
from contexts.knowledge.domain.errors import (
    GraphRagBuildBusy,
    GraphRagResetCompensationFailed,
)
from contexts.knowledge.domain.graphrag import (
    IN_FLIGHT_BUILD_STATES,
    BuildState,
    GraphRagConfig,
)
from tests.unit.graph_reset_fakes import (
    FakeLockStore,
    FakeNeo4j,
    FakeSnapshotStore,
    RecordingDb,
)


class FakeRepo:
    def __init__(self, cfg: GraphRagConfig) -> None:
        self._cfg = cfg
        self.sets: list[dict[str, Any]] = []

    async def get(self, _id: uuid.UUID, *, include_deleted: bool = False):
        return self._cfg

    async def set_state(self, **kw: Any) -> None:
        self.sets.append(kw)
        self._cfg = GraphRagConfig(
            id=self._cfg.id,
            project_id=self._cfg.project_id,
            agent_id=self._cfg.agent_id,
            builder_key_group_id=self._cfg.builder_key_group_id,
            trigger_config=self._cfg.trigger_config,
            last_build_at=self._cfg.last_build_at,
            last_build_state=kw["state"],
            last_build_error=kw.get("error"),
            created_at=self._cfg.created_at,
            deleted_at=self._cfg.deleted_at,
        )


def _replace_state(cfg: GraphRagConfig, state: BuildState) -> GraphRagConfig:
    import dataclasses

    return dataclasses.replace(cfg, last_build_state=state)


def _cfg(state: BuildState) -> GraphRagConfig:
    return GraphRagConfig(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        builder_key_group_id=uuid.uuid4(),
        trigger_config={},
        last_build_at=None,
        last_build_state=state,
        last_build_error="qdrant down",
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


def _service(
    db: RecordingDb,
    cfg: GraphRagConfig,
    *,
    locks: FakeLockStore,
    snaps: FakeSnapshotStore,
    neo4j: FakeNeo4j,
) -> tuple[GraphRagConfigService, FakeRepo]:
    service = GraphRagConfigService(db, snapshot_store=snaps, lock_store=locks, neo4j=neo4j)  # type: ignore[arg-type]
    repo = FakeRepo(cfg)
    service._configs = repo  # type: ignore[assignment, attr-defined]
    return service, repo


async def _reset(service: GraphRagConfigService, cfg: GraphRagConfig, *, force: bool = False):
    return await service.admin_reset(
        config_id=cfg.id,
        actor_user_id=uuid.uuid4(),
        actor_ip="127.0.0.1",
        force=force,
        request_id=uuid.uuid4(),
    )


# ===========================================================================
# 1. Discard of an in-flight build (red-first, AC-1/AC-2)
# ===========================================================================


@pytest.mark.asyncio
async def test_reset_discards_failed_compensating_build() -> None:
    cfg = _cfg(BuildState.FAILED_COMPENSATING)
    build_id = uuid.uuid4()
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=build_id, snapshot={"nodes": [], "edges": []})
    neo4j = FakeNeo4j()
    service, repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    out = await _reset(service, cfg)

    assert locks.acquired is True
    assert locks.released is True
    assert neo4j.deleted == [build_id]
    assert neo4j.restored == [{"nodes": [], "edges": []}]
    assert snaps.deleted == [build_id]
    assert snaps.cleared is True
    assert repo.sets[-1]["state"] is BuildState.IDLE
    assert repo.sets[-1]["error"] is None
    assert out.last_build_state is BuildState.IDLE


# ===========================================================================
# 2. Idempotent reset of a clean config (AC-3)
# ===========================================================================


@pytest.mark.asyncio
async def test_reset_idempotent_on_clean_config() -> None:
    cfg = _cfg(BuildState.IDLE)
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=None, snapshot=None)
    neo4j = FakeNeo4j()
    service, repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    out = await _reset(service, cfg)

    assert neo4j.deleted == []
    assert neo4j.restored == []
    assert snaps.deleted == []
    assert snaps.cleared is True  # stale pointer cleared unconditionally
    assert repo.sets[-1]["state"] is BuildState.IDLE
    assert locks.released is True
    assert out.last_build_state is BuildState.IDLE


# ===========================================================================
# 3. force=false compensation failure refuses (AC-4)
# ===========================================================================


@pytest.mark.asyncio
async def test_force_false_compensation_failure_refuses_and_keeps_material() -> None:
    cfg = _cfg(BuildState.FAILED_COMPENSATING)
    build_id = uuid.uuid4()
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=build_id, snapshot={"nodes": [{"name": "A"}]})
    neo4j = FakeNeo4j(raise_on_restore=True)
    db = RecordingDb()
    service, repo = _service(db, cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with pytest.raises(GraphRagResetCompensationFailed):
        await _reset(service, cfg, force=False)

    assert repo.sets == []  # NOT forced to idle
    assert snaps.deleted == []  # recovery material preserved
    assert snaps.cleared is False  # current pointer preserved
    assert locks.released is True
    assert db.committed is True  # audit persisted before the 5xx unwinds the txn


# ===========================================================================
# 4. force=true compensation failure unsticks honestly, without re-opening reads
# ===========================================================================


@pytest.mark.asyncio
async def test_force_true_compensation_failure_stays_read_blocked() -> None:
    """Having the recovery material does not make an unfinished rollback readable.

    delete_by_build has already removed the failed build's rows by the time the restore
    raises, so the subgraph is missing what the rollback owed it. Landing on IDLE would
    publish that as healthy, and IDLE is outside the reconciler sweep set, so nothing
    would ever revisit it. force still unsticks: RECOVERY_UNAVAILABLE is manually
    rebuildable. The material is retained so a later reset can retry compensation.
    """
    cfg = _cfg(BuildState.FAILED_COMPENSATING)
    build_id = uuid.uuid4()
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=build_id, snapshot={"nodes": [{"name": "A"}]})
    neo4j = FakeNeo4j(raise_on_restore=True)
    service, repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with patch(
        "contexts.knowledge.application.graph_admin_reset.audit.emit",
        new_callable=AsyncMock,
    ) as emit:
        out = await _reset(service, cfg, force=True)

    assert out.last_build_state is BuildState.RECOVERY_UNAVAILABLE
    assert out.last_build_state in IN_FLIGHT_BUILD_STATES  # never served
    assert repo.sets[-1]["state"] is BuildState.RECOVERY_UNAVAILABLE
    assert repo.sets[-1]["error"] is not None  # honest, non-null residue flag
    assert snaps.deleted == []  # residue preserved (FU-5)
    assert snaps.cleared is False
    meta = emit.await_args.args[1].metadata
    assert meta["outcome"] == "compensation_failed"
    assert meta["forced"] is True


# ===========================================================================
# 5. Lock contention (AC-4 / AC-5)
# ===========================================================================


@pytest.mark.asyncio
async def test_force_false_held_lock_raises_busy() -> None:
    cfg = _cfg(BuildState.RUNNING)
    locks = FakeLockStore(held=True)
    snaps = FakeSnapshotStore(current=uuid.uuid4(), snapshot={})
    neo4j = FakeNeo4j()
    service, repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with pytest.raises(GraphRagBuildBusy):
        await _reset(service, cfg, force=False)

    assert repo.sets == []  # no state change
    assert locks.force_released is False
    assert neo4j.deleted == []


@pytest.mark.asyncio
async def test_force_true_held_lock_force_releases_and_proceeds() -> None:
    cfg = _cfg(BuildState.RUNNING)
    locks = FakeLockStore(held=True)
    snaps = FakeSnapshotStore(current=None, snapshot=None)
    neo4j = FakeNeo4j()
    service, repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    out = await _reset(service, cfg, force=True)

    assert locks.force_released is True
    # RUNNING with no pointer is an unresolvable in-flight build, so the forced reset
    # lands read-blocked rather than idle (see the force-semantics test above).
    assert repo.sets[-1]["state"] is BuildState.RECOVERY_UNAVAILABLE
    assert out.last_build_state is BuildState.RECOVERY_UNAVAILABLE


# ===========================================================================
# 6. Audit metadata on the happy path (AC-6)
# ===========================================================================


@pytest.mark.asyncio
async def test_audit_metadata_carries_state_build_forced_outcome() -> None:
    cfg = _cfg(BuildState.NEO4J_COMMITTED)
    build_id = uuid.uuid4()
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=build_id, snapshot={"nodes": [], "edges": []})
    neo4j = FakeNeo4j()
    service, _repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with patch(
        "contexts.knowledge.application.graph_admin_reset.audit.emit",
        new_callable=AsyncMock,
    ) as emit:
        await _reset(service, cfg)

    event = emit.await_args.args[1]
    assert event.action == "admin.graphrag_reset"
    assert event.metadata["previous_state"] == BuildState.NEO4J_COMMITTED.value
    assert event.metadata["build_id"] == str(build_id)
    assert event.metadata["forced"] is False
    assert event.metadata["outcome"] == "discarded"


# ===========================================================================
# 7. Missing recovery material fails closed
#    (2026-07-17-graphrag-reset-expired-recovery, AC-1/AC-2/AC-3)
#
# The Redis pointer and snapshot are two independently expiring keys, and the
# pointer is written last (graphrag_builder.py:282-292), so it outlives the
# snapshot. Compensating an in-flight build with the snapshot already gone is
# impossible: delete_by_build would strip the build's nodes with nothing to
# restore the pre-build subgraph from. Default reset must refuse rather than
# report a successful discard over a truncated graph.
# ===========================================================================


_IN_FLIGHT = [BuildState.NEO4J_COMMITTED, BuildState.FAILED_COMPENSATING, BuildState.RUNNING]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", _IN_FLIGHT)
async def test_missing_snapshot_refuses_and_touches_nothing(state: BuildState) -> None:
    """Pointer present, snapshot expired — no delete-only rollback, no idle."""
    cfg = _cfg(state)
    build_id = uuid.uuid4()
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=build_id, snapshot=None)
    neo4j = FakeNeo4j()
    db = RecordingDb()
    service, repo = _service(db, cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with pytest.raises(GraphRagResetCompensationFailed):
        await _reset(service, cfg, force=False)

    assert neo4j.deleted == []  # the destructive half never runs
    assert neo4j.restored == []
    assert repo.sets == []  # never advertises idle
    assert snaps.deleted == []  # recovery material untouched
    assert snaps.cleared is False  # pointer survives for a later forced reset
    assert locks.released is True
    assert db.committed is True  # audit durable before the 5xx unwinds the txn


@pytest.mark.asyncio
@pytest.mark.parametrize("state", _IN_FLIGHT)
async def test_missing_pointer_on_in_flight_state_refuses(state: BuildState) -> None:
    """No pointer while the config claims an in-flight build — unresolvable."""
    cfg = _cfg(state)
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=None, snapshot=None)
    neo4j = FakeNeo4j()
    db = RecordingDb()
    service, repo = _service(db, cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with pytest.raises(GraphRagResetCompensationFailed):
        await _reset(service, cfg, force=False)

    assert neo4j.deleted == []
    assert neo4j.restored == []
    assert repo.sets == []
    assert snaps.cleared is False
    assert locks.released is True
    assert db.committed is True


@pytest.mark.asyncio
async def test_state_is_re_read_under_the_lock_not_taken_from_the_caller() -> None:
    """The compensation decision must use the state as of lock acquisition.

    The caller fetches the config before taking the lock (to raise its own not-found),
    and that read races the builder: a build can start, crash, and leave a current-build
    pointer in the gap. Deciding on the pre-lock state would classify that crashed build
    as a settled config and take the delete-without-restore path -- reporting a clean
    discard over a graph that just lost its pre-build state.
    """
    cfg = _cfg(BuildState.IDLE)  # what the caller sees before the lock
    build_id = uuid.uuid4()
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=build_id, snapshot=None)
    neo4j = FakeNeo4j()
    db = RecordingDb()
    service, repo = _service(db, cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    # First get (the caller's pre-lock read) sees the settled config; every later get
    # sees the crashed build the builder left behind while the lock was being taken.
    reads = {"n": 0}
    settled, crashed = cfg, _replace_state(cfg, BuildState.RUNNING)

    async def _racing_get(_id: uuid.UUID, *, include_deleted: bool = False) -> GraphRagConfig:
        reads["n"] += 1
        return settled if reads["n"] == 1 else crashed

    repo.get = _racing_get  # type: ignore[assignment]  # simulates the pre-lock/post-lock race

    with pytest.raises(GraphRagResetCompensationFailed):
        await _reset(service, cfg, force=False)

    assert reads["n"] >= 2, "the reset must re-read the config after taking the lock"

    assert neo4j.deleted == [], "must not delete against a stale settled-state decision"
    assert snaps.cleared is False
    assert repo.sets == []


@pytest.mark.asyncio
async def test_unavailable_audit_metadata_is_distinct_and_carries_no_secrets() -> None:
    cfg = _cfg(BuildState.NEO4J_COMMITTED)
    build_id = uuid.uuid4()
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=build_id, snapshot=None)
    neo4j = FakeNeo4j()
    service, _repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with (
        patch(
            "contexts.knowledge.application.graph_admin_reset.audit.emit",
            new_callable=AsyncMock,
        ) as emit,
        pytest.raises(GraphRagResetCompensationFailed),
    ):
        await _reset(service, cfg, force=False)

    meta = emit.await_args.args[1].metadata
    assert meta["outcome"] == "compensation_unavailable"
    assert meta["forced"] is False
    assert meta["previous_state"] == BuildState.NEO4J_COMMITTED.value
    assert meta["build_id"] == str(build_id)  # known build id is recorded
    # Audit carries identifiers only — never snapshot contents or error text.
    assert set(meta) == {"previous_state", "project_id", "build_id", "forced", "outcome"}


@pytest.mark.asyncio
async def test_missing_pointer_audit_records_null_build_id() -> None:
    cfg = _cfg(BuildState.FAILED_COMPENSATING)
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=None, snapshot=None)
    neo4j = FakeNeo4j()
    service, _repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with (
        patch(
            "contexts.knowledge.application.graph_admin_reset.audit.emit",
            new_callable=AsyncMock,
        ) as emit,
        pytest.raises(GraphRagResetCompensationFailed),
    ):
        await _reset(service, cfg, force=False)

    meta = emit.await_args.args[1].metadata
    assert meta["outcome"] == "compensation_unavailable"
    assert meta["build_id"] is None


@pytest.mark.asyncio
async def test_force_true_clears_the_stuck_state_but_stays_read_blocked() -> None:
    """force accepts the loss and unsticks the config -- but not by advertising it healthy.

    Forcing IDLE here would re-open reads on a partially applied build that no rollback
    can undo, which is exactly what RECOVERY_UNAVAILABLE exists to prevent. It also buys
    nothing: that state is already accepted by the manual build endpoint and the engine,
    so a rebuild is available either way.
    """
    cfg = _cfg(BuildState.NEO4J_COMMITTED)
    build_id = uuid.uuid4()
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=build_id, snapshot=None)
    neo4j = FakeNeo4j()
    service, repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with patch(
        "contexts.knowledge.application.graph_admin_reset.audit.emit",
        new_callable=AsyncMock,
    ) as emit:
        out = await _reset(service, cfg, force=True)

    assert out.last_build_state is BuildState.RECOVERY_UNAVAILABLE
    assert out.last_build_state in IN_FLIGHT_BUILD_STATES  # still unreadable
    assert repo.sets[-1]["error"] is not None  # honest residue flag
    meta = emit.await_args.args[1].metadata
    assert meta["outcome"] == "compensation_unavailable"
    assert meta["forced"] is True


@pytest.mark.asyncio
async def test_force_true_landing_states_converge_on_any_unfinished_rollback() -> None:
    """The two forced-failure paths are one rule now: outcome, not plan.

    Section 4 covers a failed COMPENSATE and the test above covers UNAVAILABLE; this
    pins the property they share, so a future change that re-splits them by plan fails
    here rather than only in whichever path it broke.
    """
    for prev, snapshot in (
        (BuildState.FAILED_COMPENSATING, {"nodes": [{"name": "A"}]}),
        (BuildState.NEO4J_COMMITTED, None),
    ):
        cfg = _cfg(prev)
        snaps = FakeSnapshotStore(current=uuid.uuid4(), snapshot=snapshot)
        service, repo = _service(
            RecordingDb(),
            cfg,
            locks=FakeLockStore(),
            snaps=snaps,
            neo4j=FakeNeo4j(raise_on_restore=True),
        )

        out = await _reset(service, cfg, force=True)

        assert out.last_build_state is BuildState.RECOVERY_UNAVAILABLE
        assert out.last_build_state in IN_FLIGHT_BUILD_STATES
        assert repo.sets[-1]["error"] is not None
