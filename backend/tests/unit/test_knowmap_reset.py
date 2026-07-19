"""Admin reset for Knowledge Map configs (R11a.02).

Knowledge Maps had no reset until 2026-07-17-graphrag-reset-expired-recovery (AC-10).
Both products bind the same ``perform_admin_reset``, so these tests deliberately assert
the *same* observable contract as ``test_graphrag_reset.py`` rather than re-deriving it:
if the two ever diverge, one of the two suites fails. The shared store doubles live in
``graph_reset_fakes`` -- not in either suite -- so neither owns the other's fixtures.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.knowledge.application.knowmap_config_service import KnowmapConfigService
from contexts.knowledge.domain.errors import (
    GraphRagBuildBusy,
    KnowmapResetCompensationFailed,
)
from contexts.knowledge.domain.graphrag import BuildState
from contexts.knowledge.domain.knowmap import ChunkStrategy, KnowmapConfig
from tests.unit.graph_reset_fakes import (
    FakeLockStore,
    FakeNeo4j,
    FakeSnapshotStore,
    RecordingDb,
)

_IN_FLIGHT = [BuildState.NEO4J_COMMITTED, BuildState.FAILED_COMPENSATING, BuildState.RUNNING]


class FakeKnowmapRepo:
    def __init__(self, cfg: KnowmapConfig) -> None:
        self._cfg = cfg
        self.sets: list[dict[str, Any]] = []

    async def get(self, _id: uuid.UUID, *, include_deleted: bool = False) -> KnowmapConfig:
        return self._cfg

    async def set_state(self, **kw: Any) -> None:
        self.sets.append(kw)
        self._cfg = _replace_state(self._cfg, kw["state"], kw.get("error"))


def _cfg(state: BuildState) -> KnowmapConfig:
    return KnowmapConfig(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="docs",
        builder_key_group_id=uuid.uuid4(),
        chunk_strategy=ChunkStrategy.FIXED,
        chunk_params={},
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        embed_dim=1536,
        last_build_at=None,
        last_build_state=state,
        last_build_error="qdrant down",
        corpus_revision=1,
        built_corpus_revision=None,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


def _replace_state(cfg: KnowmapConfig, state: BuildState, error: str | None) -> KnowmapConfig:
    import dataclasses

    return dataclasses.replace(cfg, last_build_state=state, last_build_error=error)


def _service(
    db: RecordingDb,
    cfg: KnowmapConfig,
    *,
    locks: FakeLockStore,
    snaps: FakeSnapshotStore,
    neo4j: FakeNeo4j,
) -> tuple[KnowmapConfigService, FakeKnowmapRepo]:
    service = KnowmapConfigService(db, snapshot_store=snaps, lock_store=locks, neo4j=neo4j)  # type: ignore[arg-type]
    repo = FakeKnowmapRepo(cfg)
    service._configs = repo  # type: ignore[assignment]
    return service, repo


async def _reset(service: KnowmapConfigService, cfg: KnowmapConfig, *, force: bool = False):
    return await service.admin_reset(
        config_id=cfg.id,
        actor_user_id=uuid.uuid4(),
        actor_ip="127.0.0.1",
        force=force,
        request_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_reset_discards_in_flight_build() -> None:
    cfg = _cfg(BuildState.FAILED_COMPENSATING)
    build_id = uuid.uuid4()
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=build_id, snapshot={"nodes": [], "edges": []})
    neo4j = FakeNeo4j()
    service, repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    out = await _reset(service, cfg)

    assert neo4j.deleted == [build_id]
    assert neo4j.restored == [{"nodes": [], "edges": []}]
    assert snaps.deleted == [build_id]
    assert snaps.cleared is True
    assert locks.released is True
    assert repo.sets[-1]["state"] is BuildState.IDLE
    assert out.last_build_state is BuildState.IDLE


@pytest.mark.asyncio
@pytest.mark.parametrize("state", _IN_FLIGHT)
async def test_missing_snapshot_refuses_and_touches_nothing(state: BuildState) -> None:
    """AC-10: the fail-closed contract holds identically on this product."""
    cfg = _cfg(state)
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=uuid.uuid4(), snapshot=None)
    neo4j = FakeNeo4j()
    db = RecordingDb()
    service, repo = _service(db, cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with pytest.raises(KnowmapResetCompensationFailed):
        await _reset(service, cfg, force=False)

    assert neo4j.deleted == []
    assert repo.sets == []
    assert snaps.deleted == []
    assert snaps.cleared is False
    assert locks.released is True
    assert db.committed is True


@pytest.mark.asyncio
async def test_reset_of_an_irrecoverable_config_keeps_it_read_blocked() -> None:
    """Neither reset mode makes an irrecoverable Knowledge Map readable again.

    Default reset refuses (503) because the material is gone; force accepts the loss and
    records it, but lands back on RECOVERY_UNAVAILABLE rather than IDLE, because nothing
    about forcing changes the fact that the partially applied build cannot be undone.
    The escape from this state is a rebuild (AC-9), not a reset.
    """
    cfg = _cfg(BuildState.RECOVERY_UNAVAILABLE)
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=None, snapshot=None)
    neo4j = FakeNeo4j()
    service, repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with pytest.raises(KnowmapResetCompensationFailed):
        await _reset(service, cfg, force=False)

    out = await _reset(service, cfg, force=True)

    assert out.last_build_state is BuildState.RECOVERY_UNAVAILABLE
    assert repo.sets[-1]["error"] is not None  # honest residue flag


@pytest.mark.asyncio
async def test_audit_uses_knowmap_action_and_resource_type() -> None:
    cfg = _cfg(BuildState.NEO4J_COMMITTED)
    build_id = uuid.uuid4()
    locks = FakeLockStore()
    snaps = FakeSnapshotStore(current=build_id, snapshot={"nodes": []})
    neo4j = FakeNeo4j()
    service, _repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with patch(
        "contexts.knowledge.application.graph_admin_reset.audit.emit",
        new_callable=AsyncMock,
    ) as emit:
        await _reset(service, cfg)

    event = emit.await_args.args[1]
    assert event.action == "admin.knowmap_reset"
    assert event.resource_type == "knowmap_config"
    assert event.metadata["previous_state"] == BuildState.NEO4J_COMMITTED.value
    assert event.metadata["outcome"] == "discarded"
    # Identifiers only -- same shape as the Concept Map's audit.
    assert set(event.metadata) == {"previous_state", "project_id", "build_id", "forced", "outcome"}


@pytest.mark.asyncio
@pytest.mark.parametrize("is_admin", [False, True])
async def test_admin_reset_route_requires_platform_admin(is_admin: bool) -> None:
    """The new route is admin-only, and a non-admin never reaches the service.

    Neither reset route had a route-level authz test before this task; the check is a
    bare ``principal.is_admin`` with no project scoping (FU-2), so it is worth pinning.
    """
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.v1.knowmap import admin_reset as admin_reset_route

    config_id = uuid.uuid4()
    # MagicMock, not AsyncMock: the class is called synchronously, only admin_reset awaits.
    service_cls = MagicMock()

    with patch("app.api.v1.knowmap.KnowmapConfigService", service_cls):
        service_cls.return_value.admin_reset = AsyncMock(return_value=_cfg(BuildState.IDLE))
        principal = SimpleNamespace(is_admin=is_admin, user_id=uuid.uuid4())
        ctx = SimpleNamespace(actor_ip="127.0.0.1", request_id=uuid.uuid4())

        if is_admin:
            await admin_reset_route(
                config_id=config_id, force=False, ctx=ctx, principal=principal, db=AsyncMock()
            )
            service_cls.return_value.admin_reset.assert_awaited_once()
        else:
            with pytest.raises(HTTPException) as exc:
                await admin_reset_route(
                    config_id=config_id, force=False, ctx=ctx, principal=principal, db=AsyncMock()
                )
            assert exc.value.status_code == 403
            service_cls.return_value.admin_reset.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_false_held_lock_raises_busy() -> None:
    cfg = _cfg(BuildState.RUNNING)
    locks = FakeLockStore(held=True)
    snaps = FakeSnapshotStore(current=uuid.uuid4(), snapshot={})
    neo4j = FakeNeo4j()
    service, repo = _service(RecordingDb(), cfg, locks=locks, snaps=snaps, neo4j=neo4j)

    with pytest.raises(GraphRagBuildBusy):
        await _reset(service, cfg, force=False)

    assert repo.sets == []
    assert locks.force_released is False
    assert neo4j.deleted == []
