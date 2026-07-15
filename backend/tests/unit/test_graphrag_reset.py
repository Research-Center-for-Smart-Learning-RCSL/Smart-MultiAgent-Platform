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
from contexts.knowledge.domain.graphrag import BuildState, GraphRagConfig


class RecordingDb:
    """Minimal AsyncSession double — records commits; no real audit write."""

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.committed = False

    async def execute(self, stmt: Any, *a: Any, **kw: Any) -> Any:
        self.calls.append(stmt)

        class _R:
            def one(_self) -> Any:  # noqa: N805
                return None

            def first(_self) -> Any:  # noqa: N805
                return None

            def all(_self) -> list[Any]:  # noqa: N805
                return []

        return _R()

    async def commit(self) -> None:
        self.committed = True


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


class FakeLockStore:
    def __init__(self, *, held: bool = False) -> None:
        self.held = held
        self.acquired = False
        self.released = False
        self.force_released = False

    async def acquire(self, config_id: uuid.UUID, *, ttl_s: int) -> bool:
        if self.held:
            return False
        self.acquired = True
        return True

    async def release(self, config_id: uuid.UUID) -> None:
        self.released = True

    async def force_release(self, config_id: uuid.UUID) -> None:
        self.force_released = True
        self.held = False  # after breaking the lock, re-acquire succeeds


class FakeSnapshotStore:
    def __init__(self, *, current: uuid.UUID | None = None, snapshot: dict[str, Any] | None = None) -> None:
        self.current = current
        self.snapshot = snapshot
        self.deleted: list[uuid.UUID] = []
        self.cleared = False

    async def get_current(self, *, config_id: uuid.UUID) -> uuid.UUID | None:
        return self.current

    async def get(self, *, config_id: uuid.UUID, build_id: uuid.UUID) -> dict[str, Any] | None:
        return self.snapshot

    async def delete(self, *, config_id: uuid.UUID, build_id: uuid.UUID) -> None:
        self.deleted.append(build_id)

    async def clear_current(self, *, config_id: uuid.UUID) -> None:
        self.cleared = True


class FakeNeo4j:
    def __init__(self, *, raise_on_restore: bool = False, raise_on_delete: bool = False) -> None:
        self.raise_on_restore = raise_on_restore
        self.raise_on_delete = raise_on_delete
        self.deleted: list[uuid.UUID] = []
        self.restored: list[dict[str, Any]] = []
        self.closed = False

    async def delete_by_build(self, *, config_id: uuid.UUID, build_id: uuid.UUID) -> None:
        if self.raise_on_delete:
            raise RuntimeError("neo4j delete down")
        self.deleted.append(build_id)

    async def restore_from_snapshot(self, *, config_id: uuid.UUID, snapshot: dict[str, Any]) -> None:
        if self.raise_on_restore:
            raise RuntimeError("neo4j restore down")
        self.restored.append(snapshot)

    async def close(self) -> None:
        self.closed = True


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
# 4. force=true compensation failure forces idle honestly (AC-5)
# ===========================================================================


@pytest.mark.asyncio
async def test_force_true_compensation_failure_forces_idle_with_error() -> None:
    cfg = _cfg(BuildState.FAILED_COMPENSATING)
    build_id = uuid.uuid4()
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=build_id, snapshot={"nodes": [{"name": "A"}]})
    neo4j = FakeNeo4j(raise_on_restore=True)
    service, repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with patch(
        "contexts.knowledge.application.graphrag_config_service.audit.emit",
        new_callable=AsyncMock,
    ) as emit:
        out = await _reset(service, cfg, force=True)

    assert out.last_build_state is BuildState.IDLE
    assert repo.sets[-1]["state"] is BuildState.IDLE
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
    assert repo.sets[-1]["state"] is BuildState.IDLE
    assert out.last_build_state is BuildState.IDLE


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
        "contexts.knowledge.application.graphrag_config_service.audit.emit",
        new_callable=AsyncMock,
    ) as emit:
        await _reset(service, cfg)

    event = emit.await_args.args[1]
    assert event.action == "admin.graphrag_reset"
    assert event.metadata["previous_state"] == BuildState.NEO4J_COMMITTED.value
    assert event.metadata["build_id"] == str(build_id)
    assert event.metadata["forced"] is False
    assert event.metadata["outcome"] == "discarded"
