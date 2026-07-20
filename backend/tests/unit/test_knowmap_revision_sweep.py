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
from contexts.knowledge.application.knowmap_triggers import EnqueueOutcome
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
    await repo.list_revision_divergent(limit=50)

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


@pytest.mark.asyncio
async def test_divergence_query_pages_by_keyset_not_offset() -> None:
    # The builder commits RUNNING when a build *starts*, so every config the sweep
    # hands to a worker stops matching the IDLE predicate. An OFFSET computed
    # against the earlier, larger set would then skip the rows that shifted past
    # it; `id >` is stable no matter how the set shrinks underneath.
    db = _CaptureSession()
    repo = KnowmapConfigRepository(db)  # type: ignore[arg-type]
    marker = uuid.UUID("00000000-0000-0000-0000-0000000000ff")
    await repo.list_revision_divergent(limit=50, after_id=marker)

    sql = _sql(db.statements[0])
    assert "offset" not in sql
    # literal_binds renders a UUID without its dashes.
    assert f"id>'{marker.hex}'" in sql


@pytest.mark.asyncio
async def test_divergence_query_does_not_filter_on_other_build_states() -> None:
    # Guard against a future edit widening the state predicate: RUNNING and FAILED
    # must never appear, or the sweep would fight the reconciler / retry a stop.
    db = _CaptureSession()
    repo = KnowmapConfigRepository(db)  # type: ignore[arg-type]
    await repo.list_revision_divergent(limit=1)

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
    """Stand-in session handle; counts the rollbacks that clear aborted reads."""

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


_LIVE_PROJECT = uuid.UUID(int=900001)
_DEAD_PROJECT = uuid.UUID(int=900002)


def _cfg(index: int, revision: int = 2, project_id: uuid.UUID | None = None) -> Any:
    # Ordered ids: keyset paging is defined by `id >`, so the fixture has to have
    # a deterministic order for the assertions to mean anything.
    return SimpleNamespace(
        id=uuid.UUID(int=index + 1),
        project_id=project_id or _LIVE_PROJECT,
        corpus_revision=revision,
        built_corpus_revision=revision - 1,
    )


class _FakeTenancy:
    """Stands in for the tenancy facade: only _LIVE_PROJECT still exists."""

    def __init__(self, _db: Any) -> None:
        pass

    async def get_projects(self, project_ids: Any) -> dict[uuid.UUID, Any]:
        return {p: object() for p in project_ids if p != _DEAD_PROJECT}


class _PagingRepo:
    """Serves a backlog by keyset, and can drop served rows the way production does.

    The real result set shrinks under the sweep: the builder commits RUNNING when
    a build starts, so a config a worker picks up stops matching the IDLE
    predicate. ``vanish_after_serving`` models that. A fixture that ignores it
    (a static list sliced by offset) hides exactly the paging bug that behaviour
    causes, which is how the first version of this sweep passed its tests.
    """

    backlog: ClassVar[list[Any]] = []
    calls: ClassVar[list[tuple[int, uuid.UUID | None]]] = []
    vanish_after_serving: ClassVar[bool] = False

    def __init__(self, _db: Any) -> None:
        pass

    async def list_revision_divergent(self, *, limit: int, after_id: uuid.UUID | None = None) -> list[Any]:
        type(self).calls.append((limit, after_id))
        remaining = [c for c in type(self).backlog if after_id is None or c.id > after_id]
        page = remaining[:limit]
        if type(self).vanish_after_serving:
            served = {c.id for c in page}
            type(self).backlog = [c for c in type(self).backlog if c.id not in served]
        return page

    async def list_stale_running(self, *, started_before: Any, limit: int) -> list[Any]:
        return []


def _install(monkeypatch: Any, db: _FakeDb, enqueue: Any, repo: Any = None) -> None:
    import contexts.tenancy.interfaces.facade as tenancy_facade

    monkeypatch.setattr(knowmap_task, "get_sessionmaker", lambda: (lambda: _Session(db)))
    monkeypatch.setattr(knowmap_task, "KnowmapConfigRepository", repo or _PagingRepo)
    monkeypatch.setattr(knowmap_task, "enqueue_knowmap_build", enqueue)
    monkeypatch.setattr(tenancy_facade, "TenancyFacade", _FakeTenancy)


def _reset(backlog: list[Any], *, vanish: bool = False) -> None:
    _PagingRepo.backlog = backlog
    _PagingRepo.calls = []
    _PagingRepo.vanish_after_serving = vanish


def _collector(seen: list[uuid.UUID]) -> Any:
    async def _enqueue(*, config_id: uuid.UUID, target_revision: int, pool: Any = None) -> EnqueueOutcome:
        seen.append(config_id)
        return EnqueueOutcome.QUEUED

    return _enqueue


@pytest.mark.asyncio
async def test_sweep_skips_configs_whose_project_was_deleted(monkeypatch: Any) -> None:
    # Deleting a project does not cascade deleted_at onto its knowmap configs, and
    # this cron has no membership check in front of it the way every other build
    # trigger does. Without the liveness gate it would re-read a deleted project's
    # documents, spend the tenant's provider key, and rebuild the graph they asked
    # to be rid of.
    live = _cfg(0, project_id=_LIVE_PROJECT)
    dead = _cfg(1, project_id=_DEAD_PROJECT)
    _reset([live, dead])
    seen: list[uuid.UUID] = []

    db = _FakeDb()
    _install(monkeypatch, db, _collector(seen))
    result = await knowmap_task.knowmap_revision_sweep({})

    assert seen == [live.id]
    assert result == "enqueued=1 deduped=0 failed=0 abandoned=0 stale_running=0"


@pytest.mark.asyncio
async def test_sweep_enqueues_nothing_when_liveness_cannot_be_checked(monkeypatch: Any) -> None:
    # Fail closed: an unavailable tenancy read must not be treated as "all live".
    import contexts.tenancy.interfaces.facade as tenancy_facade

    class _BrokenTenancy:
        def __init__(self, _db: Any) -> None:
            pass

        async def get_projects(self, project_ids: Any) -> dict[uuid.UUID, Any]:
            raise RuntimeError("tenancy read failed")

    _reset([_cfg(0)])
    seen: list[uuid.UUID] = []
    db = _FakeDb()
    _install(monkeypatch, db, _collector(seen))
    monkeypatch.setattr(tenancy_facade, "TenancyFacade", _BrokenTenancy)

    result = await knowmap_task.knowmap_revision_sweep({})

    assert seen == []
    assert result == "enqueued=0 deduped=0 failed=0 abandoned=0 stale_running=0"


@pytest.mark.asyncio
async def test_sweep_pages_and_stops_on_a_short_page(monkeypatch: Any) -> None:
    _reset([_cfg(i) for i in range(70)])
    seen: list[uuid.UUID] = []

    db = _FakeDb()
    _install(monkeypatch, db, _collector(seen))
    result = await knowmap_task.knowmap_revision_sweep({})

    # 70 rows over a page size of 50: a full page, then a short one that ends it.
    # The second page resumes from the last id served, not from an offset.
    assert _PagingRepo.calls == [(50, None), (50, uuid.UUID(int=50))]
    assert len(seen) == 70
    assert result == "enqueued=70 deduped=0 failed=0 abandoned=0 stale_running=0"


@pytest.mark.asyncio
async def test_sweep_skips_nothing_when_rows_leave_the_set_mid_tick(monkeypatch: Any) -> None:
    # The regression for the offset-paging bug: with rows vanishing as they are
    # served, OFFSET 50 would query past the survivors and silently drop configs
    # 51-70 while reporting a fully drained backlog.
    _reset([_cfg(i) for i in range(70)], vanish=True)
    seen: list[uuid.UUID] = []

    db = _FakeDb()
    _install(monkeypatch, db, _collector(seen))
    result = await knowmap_task.knowmap_revision_sweep({})

    assert len(seen) == 70
    assert result == "enqueued=70 deduped=0 failed=0 abandoned=0 stale_running=0"


@pytest.mark.asyncio
async def test_sweep_truncates_at_the_per_tick_cap(monkeypatch: Any) -> None:
    # AC-8: a large backlog must drain over several ticks, not arrive as one herd.
    _reset([_cfg(i) for i in range(250)])
    seen: list[uuid.UUID] = []

    db = _FakeDb()
    _install(monkeypatch, db, _collector(seen))
    result = await knowmap_task.knowmap_revision_sweep({})

    assert len(seen) == 200
    assert result == "enqueued=200 deduped=0 failed=0 abandoned=0 stale_running=0"
    # The last page is trimmed to the remaining budget rather than overshooting.
    assert sum(limit for limit, _ in _PagingRepo.calls) == 200


@pytest.mark.asyncio
async def test_sweep_targets_each_configs_own_latest_revision(monkeypatch: Any) -> None:
    # AC-3: the target is the config's committed revision, not a shared value.
    a, b = _cfg(0, revision=3), _cfg(1, revision=9)
    _reset([a, b])
    targets: dict[uuid.UUID, int] = {}

    async def _enqueue(*, config_id: uuid.UUID, target_revision: int, pool: Any = None) -> EnqueueOutcome:
        targets[config_id] = target_revision
        return EnqueueOutcome.QUEUED

    db = _FakeDb()
    _install(monkeypatch, db, _enqueue)
    await knowmap_task.knowmap_revision_sweep({})

    assert targets == {a.id: 3, b.id: 9}


@pytest.mark.asyncio
async def test_sweep_counts_each_outcome_separately(monkeypatch: Any) -> None:
    # AC-5. enqueue_knowmap_build never raises, so a sweep that only counted
    # attempts reported "enqueued=N failed=0" through a total Redis outage. The
    # tick summary has to distinguish work queued from work suppressed or lost.
    queued, deduped, failed = _cfg(0), _cfg(1), _cfg(2)
    _reset([queued, deduped, failed])
    outcomes = {
        queued.id: EnqueueOutcome.QUEUED,
        deduped.id: EnqueueOutcome.DEDUPED,
        failed.id: EnqueueOutcome.FAILED,
    }

    async def _enqueue(*, config_id: uuid.UUID, target_revision: int, pool: Any = None) -> EnqueueOutcome:
        return outcomes[config_id]

    db = _FakeDb()
    _install(monkeypatch, db, _enqueue)
    result = await knowmap_task.knowmap_revision_sweep({})

    assert result == "enqueued=1 deduped=1 failed=1 abandoned=0 stale_running=0"


@pytest.mark.asyncio
async def test_sweep_reads_the_give_up_stamps_in_one_round_trip(monkeypatch: Any) -> None:
    # A per-config GET would be 200 sequential round trips a minute at full tick.
    _reset([_cfg(i) for i in range(70)])
    seen: list[uuid.UUID] = []

    class _CountingRedis(_FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.mgets = 0

        async def mget(self, keys: list[str]) -> list[str | None]:
            self.mgets += 1
            return await super().mget(keys)

    redis = _CountingRedis()
    db = _FakeDb()
    _install(monkeypatch, db, _collector(seen))
    await knowmap_task.knowmap_revision_sweep({"redis": redis})

    assert len(seen) == 70
    assert redis.mgets == 1


@pytest.mark.asyncio
async def test_cursor_is_held_when_the_liveness_check_fails(monkeypatch: Any) -> None:
    # The tick reads a full capped page, then cannot prove project liveness and
    # offers nothing. Advancing the cursor here would hide those configs until it
    # wrapped the whole table, so the stored resume point must not move.
    import contexts.tenancy.interfaces.facade as tenancy_facade

    class _BrokenTenancy:
        def __init__(self, _db: Any) -> None:
            pass

        async def get_projects(self, project_ids: Any) -> dict[uuid.UUID, Any]:
            raise RuntimeError("tenancy read failed")

    _reset([_cfg(i) for i in range(250)])
    seen: list[uuid.UUID] = []
    redis = _FakeRedis({knowmap_task._SWEEP_CURSOR_KEY: str(uuid.UUID(int=7))})
    db = _FakeDb()
    _install(monkeypatch, db, _collector(seen))
    monkeypatch.setattr(tenancy_facade, "TenancyFacade", _BrokenTenancy)

    await knowmap_task.knowmap_revision_sweep({"redis": redis})

    assert seen == []
    assert redis.store[knowmap_task._SWEEP_CURSOR_KEY] == str(uuid.UUID(int=7))


@pytest.mark.asyncio
async def test_cursor_is_held_when_a_page_read_fails(monkeypatch: Any) -> None:
    # Clearing the cursor on a read error restarts every later tick at the lowest
    # ids, silently undoing the rotation FU-3 exists to provide.
    class _FlakyRepo(_PagingRepo):
        async def list_revision_divergent(
            self, *, limit: int, after_id: uuid.UUID | None = None
        ) -> list[Any]:
            raise RuntimeError("connection reset")

    _reset([_cfg(i) for i in range(70)])
    redis = _FakeRedis({knowmap_task._SWEEP_CURSOR_KEY: str(uuid.UUID(int=7))})
    db = _FakeDb()
    _install(monkeypatch, db, _collector([]), repo=_FlakyRepo)

    await knowmap_task.knowmap_revision_sweep({"redis": redis})

    assert redis.store[knowmap_task._SWEEP_CURSOR_KEY] == str(uuid.UUID(int=7))


@pytest.mark.asyncio
async def test_cursor_is_held_when_every_enqueue_fails(monkeypatch: Any) -> None:
    # Work that was identified but never queued must be revisited next tick.
    _reset([_cfg(i) for i in range(250)])
    redis = _FakeRedis({knowmap_task._SWEEP_CURSOR_KEY: str(uuid.UUID(int=7))})

    async def _always_fails(
        *, config_id: uuid.UUID, target_revision: int, pool: Any = None
    ) -> EnqueueOutcome:
        return EnqueueOutcome.FAILED

    db = _FakeDb()
    _install(monkeypatch, db, _always_fails)
    result = await knowmap_task.knowmap_revision_sweep({"redis": redis})

    assert "failed=200" in result
    assert redis.store[knowmap_task._SWEEP_CURSOR_KEY] == str(uuid.UUID(int=7))


@pytest.mark.asyncio
async def test_sweep_keeps_the_work_it_did_when_a_page_read_fails(monkeypatch: Any) -> None:
    # Half a tick of recovery beats none: a read failure on page 2 must not
    # discard the 50 configs page 1 already found.
    class _FlakyRepo(_PagingRepo):
        async def list_revision_divergent(
            self, *, limit: int, after_id: uuid.UUID | None = None
        ) -> list[Any]:
            if after_id is not None:
                raise RuntimeError("connection reset")
            return await super().list_revision_divergent(limit=limit, after_id=after_id)

    _reset([_cfg(i) for i in range(70)])
    seen: list[uuid.UUID] = []
    db = _FakeDb()
    _install(monkeypatch, db, _collector(seen), repo=_FlakyRepo)

    result = await knowmap_task.knowmap_revision_sweep({})

    assert len(seen) == 50
    assert result == "enqueued=50 deduped=0 failed=0 abandoned=0 stale_running=0"
    # The aborted transaction is cleared, or the reads after it fail as collateral.
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_sweep_still_reports_its_enqueues_when_the_stale_probe_fails(
    monkeypatch: Any,
) -> None:
    # The stuck-RUNNING check is observability only. It must never be able to
    # erase the report of builds the tick already queued.
    class _BrokenProbeRepo(_PagingRepo):
        async def list_stale_running(self, *, started_before: Any, limit: int) -> list[Any]:
            raise RuntimeError("query failed")

    _reset([_cfg(0)])
    seen: list[uuid.UUID] = []
    db = _FakeDb()
    _install(monkeypatch, db, _collector(seen), repo=_BrokenProbeRepo)

    result = await knowmap_task.knowmap_revision_sweep({})

    assert seen == [uuid.UUID(int=1)]
    assert result == "enqueued=1 deduped=0 failed=0 abandoned=0 stale_running=0"


@pytest.mark.asyncio
async def test_sweep_on_an_empty_backlog_is_a_no_op(monkeypatch: Any) -> None:
    _reset([])
    calls = 0

    async def _enqueue(**_k: Any) -> EnqueueOutcome:
        nonlocal calls
        calls += 1
        return EnqueueOutcome.QUEUED

    db = _FakeDb()
    _install(monkeypatch, db, _enqueue)
    result = await knowmap_task.knowmap_revision_sweep({})

    assert result == "enqueued=0 deduped=0 failed=0 abandoned=0 stale_running=0"
    assert calls == 0


# ---------------------------------------------------------------------------
# Stuck-RUNNING observation (AC-11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_running_query_predicates() -> None:
    db = _CaptureSession()
    repo = KnowmapConfigRepository(db)  # type: ignore[arg-type]
    await repo.list_stale_running(started_before=datetime(2026, 7, 20, tzinfo=UTC), limit=50)

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
    # build_started_at is set: list_stale_running excludes NULL outright, so a
    # fixture with None would stand in for a row the real query cannot return.
    stuck = SimpleNamespace(
        id=uuid.uuid4(),
        corpus_revision=4,
        built_corpus_revision=1,
        build_started_at=datetime(2026, 7, 19, tzinfo=UTC),
    )

    class _StaleRepo(_PagingRepo):
        async def list_stale_running(self, *, started_before: Any, limit: int) -> list[Any]:
            return [stuck]

    _reset([])
    enqueued: list[uuid.UUID] = []

    db = _FakeDb()
    _install(monkeypatch, db, _collector(enqueued), repo=_StaleRepo)
    result = await knowmap_task.knowmap_revision_sweep({})

    assert enqueued == []
    assert result == "enqueued=0 deduped=0 failed=0 abandoned=0 stale_running=1"


@pytest.mark.asyncio
async def test_stale_threshold_clears_both_the_build_timeout_and_recovery_latency() -> None:
    # The threshold has to sit past every legitimate cause of a long RUNNING:
    # the build's own timeout, then the reconciler's worst case (residual lock
    # TTL + one cron minute). Otherwise the warning fires on healthy builds.
    assert knowmap_task._STALE_RUNNING_AFTER_S > knowmap_task.KNOWMAP_BUILD_TIMEOUT_S + LOCK_TTL_S + 60


# ---------------------------------------------------------------------------
# Cursor rotation (FU-3), re-offer give-up (FU-5)
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal Redis surface: the cursor key and the re-offer timestamps."""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = dict(initial or {})

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.store.get(k) for k in keys]

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def enqueue_job(self, *_a: Any, **_k: Any) -> Any:
        return object()


@pytest.mark.asyncio
async def test_capped_tick_saves_a_cursor_and_the_next_one_resumes(monkeypatch: Any) -> None:
    # FU-3: without this every tick restarts at the lowest ids, so a tenant with
    # more than a tick's worth of divergent configs pins the window and configs
    # sorting above theirs are never reached.
    _reset([_cfg(i) for i in range(250)])
    seen: list[uuid.UUID] = []
    redis = _FakeRedis()
    db = _FakeDb()
    _install(monkeypatch, db, _collector(seen))

    await knowmap_task.knowmap_revision_sweep({"redis": redis})

    # Stopped on the cap, so the resume point is the last id it read.
    assert redis.store[knowmap_task._SWEEP_CURSOR_KEY] == str(uuid.UUID(int=200))

    seen.clear()
    _PagingRepo.calls = []
    await knowmap_task.knowmap_revision_sweep({"redis": redis})
    assert _PagingRepo.calls[0] == (50, uuid.UUID(int=200))


@pytest.mark.asyncio
async def test_a_tick_that_reaches_the_end_clears_the_cursor(monkeypatch: Any) -> None:
    # Wrapping is the other half of rotation: having walked to the end, the next
    # pass must go back to the beginning rather than sit past the last id.
    _reset([_cfg(i) for i in range(3)])
    redis = _FakeRedis({knowmap_task._SWEEP_CURSOR_KEY: str(uuid.UUID(int=1))})
    db = _FakeDb()
    _install(monkeypatch, db, _collector([]))

    await knowmap_task.knowmap_revision_sweep({"redis": redis})

    assert knowmap_task._SWEEP_CURSOR_KEY not in redis.store


@pytest.mark.asyncio
async def test_sweep_gives_up_on_a_config_that_never_converges(monkeypatch: Any) -> None:
    # FU-5: re-offering something that is not converging does not recover it, and
    # every cycle past keep_result is another full-corpus rebuild on the tenant's
    # own provider key.
    cfg = _cfg(0)
    _reset([cfg])
    seen: list[uuid.UUID] = []
    stale_ts = int(datetime.now(UTC).timestamp()) - knowmap_task._REOFFER_GIVE_UP_S - 60
    redis = _FakeRedis({f"knowmap:sweep:firstoffer:{cfg.id}:2": str(stale_ts)})
    db = _FakeDb()
    _install(monkeypatch, db, _collector(seen))

    result = await knowmap_task.knowmap_revision_sweep({"redis": redis})

    assert seen == []
    assert result == "enqueued=0 deduped=0 failed=0 abandoned=1 stale_running=0"


@pytest.mark.asyncio
async def test_sweep_keeps_offering_inside_the_give_up_window(monkeypatch: Any) -> None:
    cfg = _cfg(0)
    _reset([cfg])
    seen: list[uuid.UUID] = []
    recent = int(datetime.now(UTC).timestamp()) - 60
    redis = _FakeRedis({f"knowmap:sweep:firstoffer:{cfg.id}:2": str(recent)})
    db = _FakeDb()
    _install(monkeypatch, db, _collector(seen))

    result = await knowmap_task.knowmap_revision_sweep({"redis": redis})

    assert seen == [cfg.id]
    assert result == "enqueued=1 deduped=0 failed=0 abandoned=0 stale_running=0"


@pytest.mark.asyncio
async def test_give_up_bookkeeping_fails_open(monkeypatch: Any) -> None:
    # Losing recovery because the bookkeeping store blinked is the worse failure.
    class _BrokenRedis(_FakeRedis):
        async def mget(self, keys: list[str]) -> list[str | None]:
            raise RuntimeError("redis down")

    cfg = _cfg(0)
    _reset([cfg])
    seen: list[uuid.UUID] = []
    db = _FakeDb()
    _install(monkeypatch, db, _collector(seen))

    result = await knowmap_task.knowmap_revision_sweep({"redis": _BrokenRedis()})

    assert seen == [cfg.id]
    assert "abandoned=0" in result


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
