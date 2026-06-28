"""Unit tests for :class:`GraphRagBuilder` 2PC state machine (E.7 / R11.04).

Covers the four branches of the state matrix using trivial fakes:
- happy path: idle → running → neo4j_committed → qdrant_committed → idle.
- Phase-1 failure (Neo4j apply raises) → failed, nothing committed.
- Phase-2 failure (Qdrant raises) → failed_compensating, snapshot retained.
- Reconciler retry succeeds → back to idle with last_build_at stamped.
- Reconciler exhausted → rollback via snapshot → failed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from contexts.knowledge.application.graphrag_builder import GraphRagBuilder
from contexts.knowledge.application.graphrag_reconciler import (
    RETRY_BACKOFF_S,
    ReconciliationLoop,
)
from contexts.knowledge.domain.graphrag import (
    BuildState,
    GraphRagConfig,
    Triple,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _Msg:
    id: uuid.UUID
    role: str
    content: str


class FakeConfigStore:
    """In-memory stand-in for GraphRagConfigRepository + AsyncSession facade."""

    def __init__(self, cfg: GraphRagConfig) -> None:
        self.cfg = cfg
        self.transitions: list[tuple[BuildState, str | None, bool]] = []
        self.executed: list[Any] = []

    async def execute(self, stmt: Any, *a: Any, **kw: Any) -> Any:
        self.executed.append(stmt)

        class _R:
            def one(_self) -> Any:  # noqa: N805
                return None

            def first(_self) -> Any:  # noqa: N805
                return None

            def all(_self) -> list[Any]:  # noqa: N805
                return []

        return _R()

    async def get(self, _id: uuid.UUID, *, include_deleted: bool = False) -> GraphRagConfig:
        return self.cfg

    async def list_in_state(self, state: BuildState) -> list[GraphRagConfig]:
        return [self.cfg] if self.cfg.last_build_state is state else []

    async def set_state(
        self,
        *,
        config_id: uuid.UUID,
        state: BuildState,
        error: str | None = None,
        stamp_built_at: bool = False,
    ) -> None:
        self.transitions.append((state, error, stamp_built_at))
        self.cfg = GraphRagConfig(
            id=self.cfg.id,
            project_id=self.cfg.project_id,
            agent_id=self.cfg.agent_id,
            builder_key_group_id=self.cfg.builder_key_group_id,
            trigger_config=self.cfg.trigger_config,
            last_build_at=(datetime.now(UTC) if stamp_built_at else self.cfg.last_build_at),
            last_build_state=state,
            last_build_error=error,
            created_at=self.cfg.created_at,
            deleted_at=self.cfg.deleted_at,
        )


class FakeDb:
    """Just enough of AsyncSession for audit.emit + repo execution."""

    def __init__(self) -> None:
        self.executed: list[Any] = []
        self.committed = False
        self.closed = False

    async def execute(self, stmt: Any, *a: Any, **kw: Any) -> Any:
        self.executed.append(stmt)

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

    async def close(self) -> None:
        self.closed = True


class FakeLock:
    def __init__(self, *, busy: bool = False, lose_lock: bool = False) -> None:
        self.busy = busy
        self.lose_lock = lose_lock
        self.acquired: list[uuid.UUID] = []
        self.released: list[uuid.UUID] = []
        self.refreshed: list[uuid.UUID] = []

    async def acquire(self, config_id: uuid.UUID, *, ttl_s: int) -> bool:
        if self.busy:
            return False
        self.acquired.append(config_id)
        return True

    async def release(self, config_id: uuid.UUID) -> None:
        self.released.append(config_id)

    async def refresh(self, config_id: uuid.UUID, *, ttl_s: int) -> bool:
        self.refreshed.append(config_id)
        return not self.lose_lock


class FakeSnapshots:
    def __init__(self) -> None:
        self.store: dict[tuple[uuid.UUID, uuid.UUID], dict[str, Any]] = {}
        self.current: dict[uuid.UUID, uuid.UUID] = {}

    async def put(
        self, *, config_id: uuid.UUID, build_id: uuid.UUID, snapshot: dict[str, Any], ttl_s: int
    ) -> None:
        self.store[(config_id, build_id)] = snapshot

    async def get(self, *, config_id: uuid.UUID, build_id: uuid.UUID):
        return self.store.get((config_id, build_id))

    async def delete(self, *, config_id: uuid.UUID, build_id: uuid.UUID) -> None:
        self.store.pop((config_id, build_id), None)

    async def set_current(self, *, config_id: uuid.UUID, build_id: uuid.UUID, ttl_s: int) -> None:
        self.current[config_id] = build_id

    async def get_current(self, *, config_id: uuid.UUID) -> uuid.UUID | None:
        return self.current.get(config_id)

    async def clear_current(self, *, config_id: uuid.UUID) -> None:
        self.current.pop(config_id, None)

    async def scan_current(self, *, config_id: uuid.UUID) -> uuid.UUID | None:
        for cid, bid in self.store:
            if cid == config_id:
                return bid
        return None


class FakeNeo4j:
    def __init__(
        self,
        *,
        raise_on_apply: Exception | None = None,
    ) -> None:
        self.applied: list[list[Triple]] = []
        self.deleted: list[uuid.UUID] = []
        self.restored: list[dict[str, Any]] = []
        self.raise_on_apply = raise_on_apply

    async def snapshot_subgraph(self, *, config_id, build_id):
        return {"edges": []}

    async def apply_triples(self, *, config_id, build_id, triples):
        if self.raise_on_apply is not None:
            raise self.raise_on_apply
        self.applied.append(list(triples))
        return len(triples)

    async def delete_by_build(self, *, config_id, build_id) -> None:
        self.deleted.append(build_id)

    async def delete_all(self, *, config_id) -> None:
        self.deleted.append(config_id)

    async def restore_from_snapshot(self, *, config_id, snapshot) -> None:
        self.restored.append(snapshot)

    async def traverse(self, *, config_id, seed_entities, hops):
        return []


class FakeVectorStore:
    def __init__(self, *, raise_on_upsert: Exception | None = None) -> None:
        self.raise_on_upsert = raise_on_upsert
        self.upserts: list[list[Any]] = []
        self.superseded_calls: list[dict[str, Any]] = []

    async def ensure_graphrag_collection(self, project_id, *, vector_size, **_):
        return None

    async def upsert_entities(self, *, project_id, config_id, build_id, points):
        if self.raise_on_upsert is not None:
            raise self.raise_on_upsert
        self.upserts.append(list(points))

    async def search_entities(self, **_: Any):
        return []

    async def delete_by_build(self, **_: Any) -> None:
        return None

    async def delete_by_config(self, **_: Any) -> None:
        return None

    async def delete_superseded_entities(self, **kwargs: Any) -> None:
        self.superseded_calls.append(kwargs)


class FakeExtractor:
    def __init__(self, triples: list[Triple]) -> None:
        self.triples = triples
        self.calls = 0

    async def extract(self, *, config_id, builder_key_group_id, messages):
        self.calls += 1
        return list(self.triples)


class FakeDeltaLoader:
    async def load(self, *, config_id, since, mode):
        return [_Msg(id=uuid.uuid4(), role="user", content="hi")]


class FakeEmbedder:
    vector_size = 3

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


async def _embedder_factory(cfg):
    return FakeEmbedder()


def _make_cfg() -> GraphRagConfig:
    return GraphRagConfig(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        builder_key_group_id=uuid.uuid4(),
        trigger_config={},
        last_build_at=None,
        last_build_state=BuildState.IDLE,
        last_build_error=None,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


def _make_triples() -> list[Triple]:
    return [
        Triple(
            subject="alice",
            relation="knows",
            object="bob",
            confidence=0.9,
            evidence_msg_ids=(uuid.uuid4(),),
        ),
    ]


def _make_builder(
    *,
    cfg: GraphRagConfig,
    neo4j: FakeNeo4j,
    vectors: FakeVectorStore,
    lock: FakeLock,
    snapshots: FakeSnapshots,
    extractor: FakeExtractor,
) -> tuple[GraphRagBuilder, FakeConfigStore, FakeDb]:
    db = FakeDb()
    store = FakeConfigStore(cfg)
    builder = GraphRagBuilder(
        db,  # type: ignore[arg-type]
        neo4j=neo4j,
        vector_store=vectors,  # type: ignore[arg-type]
        extractor=extractor,
        lock_store=lock,
        snapshot_store=snapshots,
        delta_loader=FakeDeltaLoader(),
        embedder_factory=_embedder_factory,
    )
    # Swap the real repo out for the fake (same public surface).
    builder._configs = store  # type: ignore[assignment, attr-defined]
    return builder, store, db


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_transitions_to_idle() -> None:
    cfg = _make_cfg()
    neo4j, vectors = FakeNeo4j(), FakeVectorStore()
    lock, snaps = FakeLock(), FakeSnapshots()
    extractor = FakeExtractor(_make_triples())
    builder, store, _db = _make_builder(
        cfg=cfg,
        neo4j=neo4j,
        vectors=vectors,
        lock=lock,
        snapshots=snaps,
        extractor=extractor,
    )

    result = await builder.run(config_id=cfg.id, mode="delta", triggered_by="manual")

    states = [t[0] for t in store.transitions]
    assert BuildState.RUNNING in states
    assert BuildState.NEO4J_COMMITTED in states
    assert BuildState.QDRANT_COMMITTED in states
    assert states[-1] is BuildState.IDLE
    assert result.state is BuildState.IDLE
    assert result.triples_written == 1
    assert result.entities_written == 2  # alice + bob
    assert neo4j.applied == [extractor.triples]
    assert lock.released == [cfg.id]
    assert not snaps.store  # cleaned on success

    # DOM-8: the build supersedes prior-build copies of exactly the entities
    # it re-embedded (alice + bob), tagged with this build's id.
    assert len(vectors.superseded_calls) == 1
    sweep = vectors.superseded_calls[0]
    assert sorted(sweep["entities"]) == ["alice", "bob"]
    assert sweep["keep_build_id"] == result.build_id
    assert sweep["config_id"] == cfg.id


# ---------------------------------------------------------------------------
# Phase-1 failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase1_failure_marks_failed_and_cleans_snapshot() -> None:
    cfg = _make_cfg()
    neo4j = FakeNeo4j(raise_on_apply=RuntimeError("cypher boom"))
    vectors = FakeVectorStore()
    lock, snaps = FakeLock(), FakeSnapshots()
    builder, store, _db = _make_builder(
        cfg=cfg,
        neo4j=neo4j,
        vectors=vectors,
        lock=lock,
        snapshots=snaps,
        extractor=FakeExtractor(_make_triples()),
    )

    result = await builder.run(config_id=cfg.id)

    assert result.state is BuildState.FAILED
    assert result.error is not None
    assert "cypher boom" in result.error
    assert store.cfg.last_build_state is BuildState.FAILED
    assert not snaps.store
    assert vectors.upserts == []
    assert vectors.superseded_calls == []  # DOM-8: a failed build sweeps nothing


# ---------------------------------------------------------------------------
# Phase-2 failure → failed_compensating (snapshot preserved)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase2_failure_enters_compensating_and_keeps_snapshot() -> None:
    cfg = _make_cfg()
    neo4j = FakeNeo4j()
    vectors = FakeVectorStore(raise_on_upsert=RuntimeError("qdrant down"))
    lock, snaps = FakeLock(), FakeSnapshots()
    builder, store, _db = _make_builder(
        cfg=cfg,
        neo4j=neo4j,
        vectors=vectors,
        lock=lock,
        snapshots=snaps,
        extractor=FakeExtractor(_make_triples()),
    )

    result = await builder.run(config_id=cfg.id)

    assert result.state is BuildState.FAILED_COMPENSATING
    assert store.cfg.last_build_state is BuildState.FAILED_COMPENSATING
    # Snapshot must survive — reconciler needs it.
    assert snaps.store
    # DOM-8: Phase-2 never reached QDRANT_COMMITTED, so no sweep ran.
    assert vectors.superseded_calls == []


# ---------------------------------------------------------------------------
# Reconciler — successful phase-2 retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciler_retry_succeeds() -> None:
    cfg = _make_cfg()
    neo4j = FakeNeo4j()
    vectors = FakeVectorStore(raise_on_upsert=RuntimeError("qdrant down"))
    lock, snaps = FakeLock(), FakeSnapshots()
    builder, store, _db = _make_builder(
        cfg=cfg,
        neo4j=neo4j,
        vectors=vectors,
        lock=lock,
        snapshots=snaps,
        extractor=FakeExtractor(_make_triples()),
    )
    await builder.run(config_id=cfg.id)
    assert store.cfg.last_build_state is BuildState.FAILED_COMPENSATING

    # Build the reconciler over the same fakes.
    attempts: list[int] = []

    async def phase2(*, cfg, build_id) -> None:
        attempts.append(1)
        # Succeed on the second retry.
        if len(attempts) < 2:
            raise RuntimeError("still down")

    async def fake_sleep(_s: float) -> None:
        return None

    recon = ReconciliationLoop(
        session_factory=lambda: store,  # type: ignore[arg-type, return-value]
        neo4j=neo4j,
        vector_store=vectors,  # type: ignore[arg-type]
        snapshot_store=snaps,
        phase2_retry=phase2,
        sleeper=fake_sleep,
    )
    # Swap the repo lookup to our fake store — the reconciler's internal
    # list_in_state call goes through a real repo normally; patch the
    # factory-returned "db" to be `store` which implements list_in_state.
    # Also stub commit/close.
    store.commit = _noop  # type: ignore[attr-defined]
    store.close = _noop  # type: ignore[attr-defined]

    # Monkey-patch the repo class used internally to route through `store`.
    from contexts.knowledge.application import graphrag_reconciler as rmod

    class _RepoShim:
        def __init__(self, db: Any) -> None:
            self._store = db

        async def list_in_state(self, state):
            return await self._store.list_in_state(state)

        async def set_state(self, **kw):
            await self._store.set_state(**kw)

    rmod.GraphRagConfigRepository = _RepoShim  # type: ignore[assignment, misc]

    touched = await recon.run_once()
    assert touched == [cfg.id]
    assert store.cfg.last_build_state is BuildState.IDLE  # type: ignore[comparison-overlap]
    assert store.cfg.last_build_at is not None  # type: ignore[unreachable]


# ---------------------------------------------------------------------------
# Reconciler — retries exhausted → rollback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciler_exhausted_rolls_back() -> None:
    cfg = _make_cfg()
    neo4j = FakeNeo4j()
    vectors = FakeVectorStore(raise_on_upsert=RuntimeError("qdrant down"))
    lock, snaps = FakeLock(), FakeSnapshots()
    builder, store, _db = _make_builder(
        cfg=cfg,
        neo4j=neo4j,
        vectors=vectors,
        lock=lock,
        snapshots=snaps,
        extractor=FakeExtractor(_make_triples()),
    )
    await builder.run(config_id=cfg.id)

    async def always_fails(*, cfg, build_id) -> None:
        raise RuntimeError("still down")

    async def fake_sleep(_s: float) -> None:
        return None

    recon = ReconciliationLoop(
        session_factory=lambda: store,  # type: ignore[arg-type, return-value]
        neo4j=neo4j,
        vector_store=vectors,  # type: ignore[arg-type]
        snapshot_store=snaps,
        phase2_retry=always_fails,
        sleeper=fake_sleep,
    )
    store.commit = _noop  # type: ignore[attr-defined]
    store.close = _noop  # type: ignore[attr-defined]
    from contexts.knowledge.application import graphrag_reconciler as rmod

    class _RepoShim:
        def __init__(self, db: Any) -> None:
            self._store = db

        async def list_in_state(self, state):
            return await self._store.list_in_state(state)

        async def set_state(self, **kw):
            await self._store.set_state(**kw)

    rmod.GraphRagConfigRepository = _RepoShim  # type: ignore[assignment, misc]

    await recon.run_once()
    assert store.cfg.last_build_state is BuildState.FAILED
    # Rollback was attempted.
    assert neo4j.deleted  # delete_by_build called


@pytest.mark.asyncio
async def test_publishes_build_state_on_each_transition(monkeypatch: Any) -> None:
    # The builder emits a WS ``build.state`` per transition so the frontend can
    # show live progress instead of polling (R11.04).
    published: list[str] = []

    async def _capture(config_id: Any, state: str, **_kw: Any) -> None:
        published.append(state)

    from contexts.knowledge.application import graphrag_builder as bmod

    monkeypatch.setattr(bmod, "publish_build_state", _capture)

    cfg = _make_cfg()
    builder, _store, _db = _make_builder(
        cfg=cfg,
        neo4j=FakeNeo4j(),
        vectors=FakeVectorStore(),
        lock=FakeLock(),
        snapshots=FakeSnapshots(),
        extractor=FakeExtractor(_make_triples()),
    )

    await builder.run(config_id=cfg.id)

    assert published[0] == BuildState.RUNNING.value
    assert BuildState.NEO4J_COMMITTED.value in published
    assert published[-1] == BuildState.IDLE.value


@pytest.mark.asyncio
async def test_publishes_failed_state_on_phase1_failure(monkeypatch: Any) -> None:
    published: list[str] = []

    async def _capture(config_id: Any, state: str, **_kw: Any) -> None:
        published.append(state)

    from contexts.knowledge.application import graphrag_builder as bmod

    monkeypatch.setattr(bmod, "publish_build_state", _capture)

    cfg = _make_cfg()
    builder, _store, _db = _make_builder(
        cfg=cfg,
        neo4j=FakeNeo4j(raise_on_apply=RuntimeError("boom")),
        vectors=FakeVectorStore(),
        lock=FakeLock(),
        snapshots=FakeSnapshots(),
        extractor=FakeExtractor(_make_triples()),
    )

    await builder.run(config_id=cfg.id)

    assert published[-1] == BuildState.FAILED.value


async def _noop(*_a: Any, **_kw: Any) -> None:
    return None


def test_retry_backoff_tuple_is_5_steps() -> None:
    assert len(RETRY_BACKOFF_S) == 5
    assert RETRY_BACKOFF_S == (1.0, 2.0, 4.0, 8.0, 16.0)
