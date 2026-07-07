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

from contexts.knowledge.application.graphrag_builder import GraphRagBuilder, ResolvedEmbedder
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
    source_member_id: uuid.UUID | None = None


class FakeConfigStore:
    """In-memory stand-in for GraphRagConfigRepository + AsyncSession facade."""

    def __init__(self, cfg: GraphRagConfig) -> None:
        self.cfg = cfg
        self.transitions: list[tuple[BuildState, str | None, bool]] = []
        self.executed: list[Any] = []
        self.list_all_ids_calls: list[bool] = []
        self.embed_pins: list[tuple[str, str, int]] = []

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

    async def list_all_ids(self, *, include_deleted: bool = False) -> set[uuid.UUID]:
        self.list_all_ids_calls.append(include_deleted)
        return {self.cfg.id}

    async def set_state(
        self,
        *,
        config_id: uuid.UUID,
        state: BuildState,
        error: str | None = None,
        stamp_built_at: bool = False,
        built_at: datetime | None = None,
    ) -> None:
        self.transitions.append((state, error, stamp_built_at))
        if built_at is not None:
            new_built_at = built_at
        elif stamp_built_at:
            new_built_at = datetime.now(UTC)
        else:
            new_built_at = self.cfg.last_build_at
        self.cfg = GraphRagConfig(
            id=self.cfg.id,
            project_id=self.cfg.project_id,
            agent_id=self.cfg.agent_id,
            builder_key_group_id=self.cfg.builder_key_group_id,
            trigger_config=self.cfg.trigger_config,
            last_build_at=new_built_at,
            last_build_state=state,
            last_build_error=error,
            created_at=self.cfg.created_at,
            deleted_at=self.cfg.deleted_at,
            embed_provider=self.cfg.embed_provider,
            embed_model=self.cfg.embed_model,
            embed_dim=self.cfg.embed_dim,
        )

    async def set_embed_pin(
        self,
        *,
        config_id: uuid.UUID,
        provider: str,
        model: str,
        dim: int,
    ) -> None:
        self.embed_pins.append((provider, model, dim))


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
        self.puts: list[uuid.UUID] = []

    async def put(
        self, *, config_id: uuid.UUID, build_id: uuid.UUID, snapshot: dict[str, Any], ttl_s: int
    ) -> None:
        self.store[(config_id, build_id)] = snapshot
        self.puts.append(build_id)

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
        config_ids: list[tuple[uuid.UUID, uuid.UUID | None]] | None = None,
    ) -> None:
        self.applied: list[list[Triple]] = []
        self.applied_project_ids: list[uuid.UUID] = []
        self.deleted: list[uuid.UUID] = []
        self.deleted_all: list[uuid.UUID] = []
        self.restored: list[dict[str, Any]] = []
        self.raise_on_apply = raise_on_apply
        self.config_ids = config_ids or []

    async def snapshot_subgraph(self, *, config_id, build_id):
        return {"edges": []}

    async def apply_triples(self, *, config_id, project_id, build_id, triples):
        if self.raise_on_apply is not None:
            raise self.raise_on_apply
        self.applied.append(list(triples))
        self.applied_project_ids.append(project_id)
        return len(triples)

    async def delete_by_build(self, *, config_id, build_id) -> None:
        self.deleted.append(build_id)

    async def delete_all(self, *, config_id) -> None:
        self.deleted.append(config_id)
        self.deleted_all.append(config_id)

    async def restore_from_snapshot(self, *, config_id, snapshot) -> None:
        self.restored.append(snapshot)

    async def traverse(self, *, config_id, seed_entities, hops):
        return []

    async def list_config_ids(self) -> list[tuple[uuid.UUID, uuid.UUID | None]]:
        return list(self.config_ids)


class FakeVectorStore:
    def __init__(self, *, raise_on_upsert: Exception | None = None) -> None:
        self.raise_on_upsert = raise_on_upsert
        self.upserts: list[list[Any]] = []
        self.superseded_calls: list[dict[str, Any]] = []
        self.deleted_by_config: list[dict[str, Any]] = []

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

    async def delete_by_config(self, **kwargs: Any) -> None:
        self.deleted_by_config.append(kwargs)

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
    async def iter_windows(self, *, config_id, since, mode):
        yield [_Msg(id=uuid.uuid4(), role="user", content="hi")]


class FakeWindowLoader:
    """Yields a fixed sequence of bounded windows (D1)."""

    def __init__(self, windows: list[list[_Msg]]) -> None:
        self.windows = windows

    async def iter_windows(self, *, config_id, since, mode):
        for window in self.windows:
            yield list(window)


def _msgs(n: int) -> list[_Msg]:
    return [_Msg(id=uuid.uuid4(), role="user", content=f"m{i}") for i in range(n)]


class FakeEmbedder:
    vector_size = 3

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


async def _embedder_factory(cfg):
    # Mirrors the worker factory contract: return the embedder plus the
    # resolved (provider, model) so the builder can self-pin a null-pin config.
    return ResolvedEmbedder(
        embedder=FakeEmbedder(),
        provider="openai",
        model="text-embedding-3-small",
    )


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
            evidence_refs=(str(uuid.uuid4()),),
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
        configs=store,  # type: ignore[arg-type]
    )
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
    # AC-7: :Entity nodes are stamped with the config's project_id so an
    # orphaned subgraph stays self-describing for the reconciler sweep.
    assert neo4j.applied_project_ids == [cfg.project_id]
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
# Phase 2b WS1 (AC-2) — member provenance threads from the delta feed onto the
# extracted relations the builder hands to Neo4j.
# ---------------------------------------------------------------------------


def _provenance_builder(
    *, cfg: GraphRagConfig, neo4j: FakeNeo4j, window: list[_Msg], triples: list[Triple]
) -> GraphRagBuilder:
    return GraphRagBuilder(
        FakeDb(),  # type: ignore[arg-type]
        neo4j=neo4j,
        vector_store=FakeVectorStore(),  # type: ignore[arg-type]
        extractor=FakeExtractor(triples),
        lock_store=FakeLock(),
        snapshot_store=FakeSnapshots(),
        delta_loader=FakeWindowLoader([window]),
        embedder_factory=_embedder_factory,
        configs=FakeConfigStore(cfg),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_build_tags_relations_with_source_member() -> None:
    cfg = _make_cfg()
    member_id, msg_id = uuid.uuid4(), uuid.uuid4()
    neo4j = FakeNeo4j()
    # The extractor cites the member's message as evidence; provenance is derived
    # from that message's source_member_id, never from the extractor output.
    triples = [
        Triple(
            subject="alice",
            relation="knows",
            object="bob",
            confidence=0.9,
            evidence_refs=(str(msg_id),),
        )
    ]
    window = [_Msg(id=msg_id, role="user", content="hi", source_member_id=member_id)]
    builder = _provenance_builder(cfg=cfg, neo4j=neo4j, window=window, triples=triples)

    await builder.run(config_id=cfg.id, mode="delta", triggered_by="manual")

    assert neo4j.applied[0][0].source_member_ids == (str(member_id),)


@pytest.mark.asyncio
async def test_build_leaves_relations_untagged_without_member_provenance() -> None:
    # A single-owner feed (messages carry no source_member_id) must not invent
    # provenance — the relation stays untagged so nothing is mis-partitioned.
    cfg = _make_cfg()
    msg_id = uuid.uuid4()
    neo4j = FakeNeo4j()
    triples = [
        Triple(
            subject="alice",
            relation="knows",
            object="bob",
            confidence=0.9,
            evidence_refs=(str(msg_id),),
        )
    ]
    window = [_Msg(id=msg_id, role="user", content="hi")]  # source_member_id=None
    builder = _provenance_builder(cfg=cfg, neo4j=neo4j, window=window, triples=triples)

    await builder.run(config_id=cfg.id, mode="delta", triggered_by="manual")

    assert neo4j.applied[0][0].source_member_ids == ()


# ---------------------------------------------------------------------------
# D2 self-pin (AC-4) — a legacy null-pin config records its embedding identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_pin_config_self_pins_after_first_build() -> None:
    cfg = _make_cfg()  # embed_dim is None → legacy/unpinned
    builder, store, _db = _make_builder(
        cfg=cfg,
        neo4j=FakeNeo4j(),
        vectors=FakeVectorStore(),
        lock=FakeLock(),
        snapshots=FakeSnapshots(),
        extractor=FakeExtractor(_make_triples()),
    )

    result = await builder.run(config_id=cfg.id)

    assert result.state is BuildState.IDLE
    # The resolved (provider, model) + the actual vector length are persisted.
    assert store.embed_pins == [("openai", "text-embedding-3-small", FakeEmbedder.vector_size)]


@pytest.mark.asyncio
async def test_already_pinned_config_does_not_self_pin() -> None:
    import dataclasses

    cfg = dataclasses.replace(
        _make_cfg(),
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        embed_dim=1536,
    )
    builder, store, _db = _make_builder(
        cfg=cfg,
        neo4j=FakeNeo4j(),
        vectors=FakeVectorStore(),
        lock=FakeLock(),
        snapshots=FakeSnapshots(),
        extractor=FakeExtractor(_make_triples()),
    )

    await builder.run(config_id=cfg.id)

    # An already-pinned config is never re-pinned by a build.
    assert store.embed_pins == []


# ---------------------------------------------------------------------------
# D1/D3 — bounded windowing + per-window lock refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_windowed_build_extracts_per_window_but_commits_once() -> None:
    cfg = _make_cfg()
    windows = [_msgs(2), _msgs(2), _msgs(1)]  # 3 bounded windows
    neo4j, vectors = FakeNeo4j(), FakeVectorStore()
    lock, snaps = FakeLock(), FakeSnapshots()
    extractor = FakeExtractor(_make_triples())
    db = FakeDb()
    store = FakeConfigStore(cfg)
    builder = GraphRagBuilder(
        db,  # type: ignore[arg-type]
        neo4j=neo4j,
        vector_store=vectors,  # type: ignore[arg-type]
        extractor=extractor,
        lock_store=lock,
        snapshot_store=snaps,
        delta_loader=FakeWindowLoader(windows),
        embedder_factory=_embedder_factory,
        configs=store,  # type: ignore[arg-type]
    )

    result = await builder.run(config_id=cfg.id)

    assert result.state is BuildState.IDLE
    # Extraction runs once per window (bounded LLM payload)...
    assert extractor.calls == len(windows)
    # ...but Neo4j apply, the snapshot, and the supersede sweep each happen once
    # for the whole build (single 2PC commit).
    assert len(neo4j.applied) == 1
    assert len(neo4j.applied[0]) == len(windows)  # one triple accumulated per window
    assert len(snaps.puts) == 1
    assert len(vectors.superseded_calls) == 1
    # D3: the lock is refreshed at every window boundary, plus once before the
    # Qdrant write, so a long extract phase cannot let the TTL lapse mid-build.
    assert len(lock.refreshed) == len(windows) + 1
    # last_build_at is advanced exactly once (the terminal IDLE transition).
    idle_stamps = [t for t in store.transitions if t[0] is BuildState.IDLE]
    assert len(idle_stamps) == 1


def test_graphrag_build_timeout_exceeds_lock_ttl() -> None:
    # D3 / AC-6: the job timeout is only a runaway backstop — it must exceed the
    # lock TTL so the continuously-refreshed lock is the single-writer authority.
    from app.workers.tasks.graphrag import GRAPHRAG_BUILD_TIMEOUT_S
    from contexts.knowledge.application.graphrag_builder import LOCK_TTL_S

    assert GRAPHRAG_BUILD_TIMEOUT_S > LOCK_TTL_S


@pytest.mark.asyncio
async def test_last_build_at_uses_started_at_watermark(monkeypatch: Any) -> None:
    # D10 / AC-13: last_build_at is stamped with the build's START time so a delta
    # arriving while the build runs is picked up by the next build (since =
    # started-at), never skipped (since = finished-at).
    from contexts.knowledge.application import graphrag_builder as bmod

    ticks = [datetime(2026, 7, 7, 12, 0, s, tzinfo=UTC) for s in range(30)]
    clock = iter(ticks)
    monkeypatch.setattr(bmod, "now", lambda: next(clock))

    cfg = _make_cfg()
    builder, store, _db = _make_builder(
        cfg=cfg,
        neo4j=FakeNeo4j(),
        vectors=FakeVectorStore(),
        lock=FakeLock(),
        snapshots=FakeSnapshots(),
        extractor=FakeExtractor(_make_triples()),
    )

    await builder.run(config_id=cfg.id)

    # The first clock read (build start) is stamped — not any later read.
    assert store.cfg.last_build_at == ticks[0]


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
        repo_factory=lambda _db: store,  # type: ignore[arg-type, return-value]
        neo4j=neo4j,
        vector_store=vectors,  # type: ignore[arg-type]
        snapshot_store=snaps,
        phase2_retry=phase2,
        sleeper=fake_sleep,
    )
    # The reconciler resolves its repo via the injected factory; point it at
    # the fake store (which implements list_in_state/set_state). Stub commit/close.
    store.commit = _noop  # type: ignore[attr-defined]
    store.close = _noop  # type: ignore[attr-defined]

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
        repo_factory=lambda _db: store,  # type: ignore[arg-type, return-value]
        neo4j=neo4j,
        vector_store=vectors,  # type: ignore[arg-type]
        snapshot_store=snaps,
        phase2_retry=always_fails,
        sleeper=fake_sleep,
    )
    store.commit = _noop  # type: ignore[attr-defined]
    store.close = _noop  # type: ignore[attr-defined]

    await recon.run_once()
    assert store.cfg.last_build_state is BuildState.FAILED
    # Rollback was attempted.
    assert neo4j.deleted  # delete_by_build called


# ---------------------------------------------------------------------------
# Reconciler — orphan sweep (AC-7): graph data whose PG row is gone is purged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciler_sweeps_orphaned_graph_configs() -> None:
    cfg = _make_cfg()  # live config — its id is in list_all_ids
    orphan_id = uuid.uuid4()
    orphan_project = uuid.uuid4()
    legacy_orphan = uuid.uuid4()  # written before :Entity carried project_id

    neo4j = FakeNeo4j(
        config_ids=[
            (cfg.id, cfg.project_id),
            (orphan_id, orphan_project),
            (legacy_orphan, None),
        ],
    )
    vectors = FakeVectorStore()
    store = FakeConfigStore(cfg)
    store.commit = _noop  # type: ignore[attr-defined]
    store.close = _noop  # type: ignore[attr-defined]

    async def never_phase2(*, cfg, build_id) -> None:  # pragma: no cover
        raise AssertionError("phase2 must not run without stuck configs")

    async def fake_sleep(_s: float) -> None:
        return None

    recon = ReconciliationLoop(
        session_factory=lambda: store,  # type: ignore[arg-type, return-value]
        repo_factory=lambda _db: store,  # type: ignore[arg-type, return-value]
        neo4j=neo4j,
        vector_store=vectors,  # type: ignore[arg-type]
        snapshot_store=FakeSnapshots(),
        phase2_retry=never_phase2,
        sleeper=fake_sleep,
    )

    touched = await recon.run_once()

    # No stuck configs → nothing healed; the sweep is the only work this cycle.
    assert touched == []
    # The sweep diffs against LIVE (non-deleted) config ids, not include_deleted:
    # a soft-deleted config whose inline purge failed is therefore reclaimed here.
    assert store.list_all_ids_calls == [False]
    # Both orphans are purged from Neo4j; the live config's subgraph is untouched.
    assert set(neo4j.deleted_all) == {orphan_id, legacy_orphan}
    assert cfg.id not in neo4j.deleted_all
    # Only the project-tagged orphan is Qdrant-sweepable; the legacy one is skipped.
    assert [d["config_id"] for d in vectors.deleted_by_config] == [orphan_id]
    assert vectors.deleted_by_config[0]["project_id"] == orphan_project


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
