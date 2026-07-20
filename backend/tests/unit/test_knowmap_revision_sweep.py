"""F-4 — the Knowledge Map revision-divergence sweep.

Finalization is edge-triggered and best-effort (``knowmap_build`` swallows a failed
``_finalize_build_revision``, and ``enqueue_knowmap_build`` swallows queue errors), so a
committed ``corpus_revision`` can have no queued build. This sweep is the level-triggered
backstop: it reads the durable gap and re-offers the work.

The AC-1 finalizer-failure regression lives in ``test_knowmap_build_dedup.py`` beside the
rest of the revision machinery; this file covers the query predicates and the sweep's
orchestration (paging, per-tick cap, failure isolation).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

import app.workers.tasks.knowmap as knowmap_task
from contexts.knowledge.application.graphrag_builder import LOCK_TTL_S
from contexts.knowledge.domain.graphrag import BuildState
from contexts.knowledge.infrastructure.graphrag_repositories import GraphRagConfigRepository
from contexts.knowledge.infrastructure.knowmap_repositories import KnowmapConfigRepository


def _sql(stmt: Any) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).replace(" ", "").lower()


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _CaptureSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        return _Result([])


# ---------------------------------------------------------------------------
# Repository predicates (AC-3, AC-4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_divergence_query_selects_only_live_idle_divergent_configs() -> None:
    db = _CaptureSession()
    repo = KnowmapConfigRepository(db)  # type: ignore[arg-type]
    await repo.list_revision_divergent(limit=50, offset=100)

    sql = _sql(db.statements[0])
    # Deleted configs are excluded (AC-4): their graph is being torn down anyway.
    assert "deleted_atisnull" in sql
    # IDLE only (Q-2): in-flight has a completion path, FAILED is a deliberate stop.
    assert "last_build_state='idle'" in sql
    # The divergence itself. COALESCE covers a config that has never built, and it
    # is also what makes revision zero unselectable without an explicit clause.
    assert "corpus_revision>coalesce(knowmap_configs.built_corpus_revision,0)" in sql
    # Stable ordering, or successive pages of one tick would overlap or skip rows.
    assert "orderbyknowmap_configs.id" in sql
    assert "limit50" in sql
    assert "offset100" in sql


@pytest.mark.asyncio
async def test_divergence_query_does_not_filter_on_other_build_states() -> None:
    # Guard against a future edit widening the state predicate: RUNNING and FAILED
    # must never appear, or the sweep would fight the reconciler / retry a stop.
    db = _CaptureSession()
    repo = KnowmapConfigRepository(db)  # type: ignore[arg-type]
    await repo.list_revision_divergent(limit=1, offset=0)

    sql = _sql(db.statements[0])
    assert "'running'" not in sql
    assert "'failed'" not in sql


# ---------------------------------------------------------------------------
# build_started_at stamping (AC-9)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("repo_cls", [KnowmapConfigRepository, GraphRagConfigRepository])
@pytest.mark.asyncio
async def test_set_state_writes_build_started_at_only_when_asked(repo_cls: Any) -> None:
    # Both repositories implement the shared set_state port and the RUNNING
    # transition lives in the shared builder, so the stamp has to work on either.
    db = _CaptureSession()
    await repo_cls(db).set_state(config_id=uuid.uuid4(), state=BuildState.RUNNING, stamp_started_at=True)
    assert "build_started_at" in db.statements[0].compile().params

    other = _CaptureSession()
    await repo_cls(other).set_state(config_id=uuid.uuid4(), state=BuildState.IDLE)
    # Untouched on every other transition: overwriting it on a terminal state
    # would make a settled config look freshly started.
    assert "build_started_at" not in other.statements[0].compile().params


# ---------------------------------------------------------------------------
# Sweep orchestration (AC-5, AC-8)
# ---------------------------------------------------------------------------


class _FakeDb:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


class _Session:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def __aenter__(self) -> _FakeDb:
        return self._db

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _cfg(revision: int = 2) -> Any:
    return SimpleNamespace(id=uuid.uuid4(), corpus_revision=revision, built_corpus_revision=revision - 1)


class _PagingRepo:
    """Serves a fixed backlog through whatever limit/offset the sweep asks for."""

    backlog: ClassVar[list[Any]] = []
    calls: ClassVar[list[tuple[int, int]]] = []

    def __init__(self, _db: Any) -> None:
        pass

    async def list_revision_divergent(self, *, limit: int, offset: int) -> list[Any]:
        type(self).calls.append((limit, offset))
        return list(type(self).backlog[offset : offset + limit])

    async def list_stale_running(self, *, started_before: Any, limit: int, offset: int) -> list[Any]:
        return []


def _install(monkeypatch: Any, db: _FakeDb, enqueue: Any) -> None:
    monkeypatch.setattr(knowmap_task, "get_sessionmaker", lambda: (lambda: _Session(db)))
    monkeypatch.setattr(knowmap_task, "KnowmapConfigRepository", _PagingRepo)
    monkeypatch.setattr(knowmap_task, "enqueue_knowmap_build", enqueue)


@pytest.mark.asyncio
async def test_sweep_pages_and_stops_on_a_short_page(monkeypatch: Any) -> None:
    _PagingRepo.backlog = [_cfg() for _ in range(70)]
    _PagingRepo.calls = []
    seen: list[uuid.UUID] = []

    async def _enqueue(*, config_id: uuid.UUID, target_revision: int) -> None:
        seen.append(config_id)

    db = _FakeDb()
    _install(monkeypatch, db, _enqueue)
    result = await knowmap_task.knowmap_revision_sweep({})

    # 70 rows over a page size of 50: a full page, then a short one that ends it.
    assert _PagingRepo.calls == [(50, 0), (50, 50)]
    assert len(seen) == 70
    assert result == "enqueued=70 failed=0 stale_running=0"


@pytest.mark.asyncio
async def test_sweep_truncates_at_the_per_tick_cap(monkeypatch: Any) -> None:
    # AC-8: a large backlog must drain over several ticks, not arrive as one herd.
    _PagingRepo.backlog = [_cfg() for _ in range(250)]
    _PagingRepo.calls = []
    seen: list[uuid.UUID] = []

    async def _enqueue(*, config_id: uuid.UUID, target_revision: int) -> None:
        seen.append(config_id)

    db = _FakeDb()
    _install(monkeypatch, db, _enqueue)
    result = await knowmap_task.knowmap_revision_sweep({})

    assert len(seen) == 200
    assert result == "enqueued=200 failed=0 stale_running=0"
    # The last page is trimmed to the remaining budget rather than overshooting.
    assert sum(limit for limit, _ in _PagingRepo.calls) == 200


@pytest.mark.asyncio
async def test_sweep_targets_each_configs_own_latest_revision(monkeypatch: Any) -> None:
    # AC-3: the target is the config's committed revision, not a shared value.
    a, b = _cfg(revision=3), _cfg(revision=9)
    _PagingRepo.backlog = [a, b]
    _PagingRepo.calls = []
    targets: dict[uuid.UUID, int] = {}

    async def _enqueue(*, config_id: uuid.UUID, target_revision: int) -> None:
        targets[config_id] = target_revision

    db = _FakeDb()
    _install(monkeypatch, db, _enqueue)
    await knowmap_task.knowmap_revision_sweep({})

    assert targets == {a.id: 3, b.id: 9}


@pytest.mark.asyncio
async def test_sweep_isolates_one_config_failure(monkeypatch: Any) -> None:
    # AC-5. enqueue_knowmap_build swallows its own queue errors today, so this
    # pins the sweep's own isolation independently of that helper's behaviour.
    boom, ok = _cfg(), _cfg()
    _PagingRepo.backlog = [boom, ok]
    _PagingRepo.calls = []
    seen: list[uuid.UUID] = []

    async def _enqueue(*, config_id: uuid.UUID, target_revision: int) -> None:
        if config_id == boom.id:
            raise RuntimeError("redis down")
        seen.append(config_id)

    db = _FakeDb()
    _install(monkeypatch, db, _enqueue)
    result = await knowmap_task.knowmap_revision_sweep({})

    assert seen == [ok.id]
    assert result == "enqueued=1 failed=1 stale_running=0"
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_sweep_on_an_empty_backlog_is_a_no_op(monkeypatch: Any) -> None:
    _PagingRepo.backlog = []
    _PagingRepo.calls = []
    calls = 0

    async def _enqueue(**_k: Any) -> None:
        nonlocal calls
        calls += 1

    db = _FakeDb()
    _install(monkeypatch, db, _enqueue)
    result = await knowmap_task.knowmap_revision_sweep({})

    assert result == "enqueued=0 failed=0 stale_running=0"
    assert calls == 0
    assert db.rollbacks == 0


# ---------------------------------------------------------------------------
# Stuck-RUNNING observation (AC-11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_running_query_predicates() -> None:
    db = _CaptureSession()
    repo = KnowmapConfigRepository(db)  # type: ignore[arg-type]
    await repo.list_stale_running(started_before=datetime(2026, 7, 20, tzinfo=UTC), limit=50, offset=0)

    sql = _sql(db.statements[0])
    assert "deleted_atisnull" in sql
    assert "last_build_state='running'" in sql
    # A pre-0059 config has no readable age; reporting it would be a guess.
    assert "build_started_atisnotnull" in sql
    assert "build_started_at<'2026-07-20" in sql


@pytest.mark.asyncio
async def test_sweep_reports_stale_running_but_never_enqueues_it(monkeypatch: Any) -> None:
    # AC-11: a RUNNING config is rejected by the builder's state whitelist anyway,
    # so offering it work would be noise. The sweep observes; the reconciler acts.
    stuck = SimpleNamespace(
        id=uuid.uuid4(), corpus_revision=4, built_corpus_revision=1, build_started_at=None
    )

    class _StaleRepo(_PagingRepo):
        async def list_stale_running(self, *, started_before: Any, limit: int, offset: int) -> list[Any]:
            return [stuck]

    _PagingRepo.backlog = []
    _PagingRepo.calls = []
    enqueued: list[uuid.UUID] = []

    async def _enqueue(*, config_id: uuid.UUID, target_revision: int) -> None:
        enqueued.append(config_id)

    db = _FakeDb()
    monkeypatch.setattr(knowmap_task, "get_sessionmaker", lambda: (lambda: _Session(db)))
    monkeypatch.setattr(knowmap_task, "KnowmapConfigRepository", _StaleRepo)
    monkeypatch.setattr(knowmap_task, "enqueue_knowmap_build", _enqueue)
    result = await knowmap_task.knowmap_revision_sweep({})

    assert enqueued == []
    assert result == "enqueued=0 failed=0 stale_running=1"


@pytest.mark.asyncio
async def test_stale_threshold_clears_both_the_build_timeout_and_recovery_latency() -> None:
    # The threshold has to sit past every legitimate cause of a long RUNNING:
    # the build's own timeout, then the reconciler's worst case (residual lock
    # TTL + one cron minute). Otherwise the warning fires on healthy builds.
    assert knowmap_task._STALE_RUNNING_AFTER_S > knowmap_task.KNOWMAP_BUILD_TIMEOUT_S + LOCK_TTL_S + 60


# ---------------------------------------------------------------------------
# Worker registration and cadence (AC-5)
# ---------------------------------------------------------------------------


def test_sweep_is_registered_as_a_worker_function() -> None:
    # A task nobody registers never runs.
    from app.workers.main import WorkerSettings

    assert knowmap_task.knowmap_revision_sweep in WorkerSettings.functions


def test_sweep_runs_every_minute() -> None:
    # No other sweep asserts its cadence; recovery latency is the whole point
    # here, so pin it.
    from app.workers.main import WorkerSettings

    jobs = [c for c in WorkerSettings.cron_jobs if c.coroutine is knowmap_task.knowmap_revision_sweep]
    assert len(jobs) == 1
    assert jobs[0].minute == set(range(60))
    assert jobs[0].run_at_startup is False
