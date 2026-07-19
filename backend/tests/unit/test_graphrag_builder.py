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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from contexts.knowledge.application.graphrag_builder import GraphRagBuilder, ResolvedEmbedder
from contexts.knowledge.application.graphrag_reconciler import (
    RETRY_BACKOFF_S,
    GraphConsumer,
    ReconciliationLoop,
)
from contexts.knowledge.domain.graphrag import (
    BuildState,
    GraphRagConfig,
    Triple,
)
from contexts.knowledge.infrastructure.channels import graphrag_channel

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _Msg:
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
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
    """Just enough of AsyncSession for audit.emit + repo execution.

    ``builder_group_project_id`` stands in for the R7.09a pre-flight's key-group
    lookup (``resolve_live_builder_group_project_id`` -> ``KeysFacade.get_key_group``
    -> ``KeyGroupRepository.get_active``, the only caller in this file that ever
    reads ``.first()`` off a query run through this fake -- ``audit.emit``'s insert
    never does): the happy-path tests wire it to the config's own project so the
    pre-flight passes; the mismatch/deleted tests set it to a different id or
    ``None``.
    """

    def __init__(self, *, builder_group_project_id: uuid.UUID | None = None) -> None:
        self.executed: list[Any] = []
        self.committed = False
        self.closed = False
        self.builder_group_project_id = builder_group_project_id

    async def execute(self, stmt: Any, *a: Any, **kw: Any) -> Any:
        self.executed.append(stmt)
        project_id = self.builder_group_project_id

        class _R:
            def one(_self) -> Any:  # noqa: N805
                return None

            def first(_self) -> Any:  # noqa: N805
                if project_id is None:
                    return None
                return SimpleNamespace(
                    id=uuid.uuid4(),
                    project_id=project_id,
                    name="builder-group",
                    created_at=datetime.now(UTC),
                    deleted_at=None,
                )

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
        raise_on_replace: Exception | None = None,
        raise_on_restore: Exception | None = None,
        config_ids: list[tuple[uuid.UUID, uuid.UUID | None]] | None = None,
        triples_for_build: list[dict[str, str]] | None = None,
    ) -> None:
        self.applied: list[list[Triple]] = []
        self.applied_project_ids: list[uuid.UUID] = []
        self.applied_replace: list[bool] = []
        self.remove_stale_calls: list[uuid.UUID] = []
        self.deleted: list[uuid.UUID] = []
        self.deleted_all: list[uuid.UUID] = []
        self.restored: list[dict[str, Any]] = []
        self.raise_on_apply = raise_on_apply
        self.raise_on_replace = raise_on_replace
        self.raise_on_restore = raise_on_restore
        self.config_ids = config_ids or []
        self.triples_for_build = triples_for_build or []

    async def snapshot_subgraph(self, *, config_id, build_id):
        return {"edges": []}

    async def apply_triples(self, *, config_id, project_id, build_id, triples):
        if self.raise_on_apply is not None:
            raise self.raise_on_apply
        self.applied.append(list(triples))
        self.applied_project_ids.append(project_id)
        self.applied_replace.append(False)
        return len(triples)

    async def replace_triples(self, *, config_id, project_id, build_id, triples):
        # Atomic in the adapter: raising here must leave `applied` untouched, the
        # way a rolled-back transaction leaves the graph untouched. A fake that
        # recorded the upsert before raising would let a builder regression pass.
        if self.raise_on_replace is not None:
            raise self.raise_on_replace
        if self.raise_on_apply is not None:
            raise self.raise_on_apply
        self.applied.append(list(triples))
        self.applied_project_ids.append(project_id)
        self.applied_replace.append(True)
        self.remove_stale_calls.append(build_id)
        return len(triples)

    async def delete_by_build(self, *, config_id, build_id) -> None:
        self.deleted.append(build_id)

    async def delete_all(self, *, config_id) -> None:
        self.deleted.append(config_id)
        self.deleted_all.append(config_id)

    async def restore_from_snapshot(self, *, config_id, snapshot) -> None:
        if self.raise_on_restore is not None:
            raise self.raise_on_restore
        self.restored.append(snapshot)

    async def traverse(self, *, config_id, seed_entities, hops):
        return []

    async def list_config_ids(self) -> list[tuple[uuid.UUID, uuid.UUID | None]]:
        return list(self.config_ids)

    async def list_triples_for_build(self, *, config_id, build_id) -> list[dict[str, str]]:
        return list(self.triples_for_build)


class FakeVectorStore:
    def __init__(
        self,
        *,
        raise_on_upsert: Exception | None = None,
        all_config_ids: set[tuple[uuid.UUID, uuid.UUID]] | None = None,
        raise_on_list_config_ids: bool = False,
        fail_points_not_in_build_times: int = 0,
    ) -> None:
        self.raise_on_upsert = raise_on_upsert
        # Finding 2: number of leading delete_points_not_in_build calls to fail
        # before succeeding (drives the folded reconciler replace-sweep retry).
        self.fail_points_not_in_build_times = fail_points_not_in_build_times
        self.upserts: list[list[Any]] = []
        self.superseded_calls: list[dict[str, Any]] = []
        self.points_not_in_build_calls: list[dict[str, Any]] = []
        self.deleted_by_config: list[dict[str, Any]] = []
        # F-8: the Qdrant-side orphan discovery index. Empty by default so tests
        # that don't exercise the sweep enumeration are unaffected.
        self._all_config_ids = all_config_ids or set()
        self.raise_on_list_config_ids = raise_on_list_config_ids

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

    async def list_all_config_ids(self) -> set[tuple[uuid.UUID, uuid.UUID]]:
        if self.raise_on_list_config_ids:
            raise RuntimeError("qdrant scroll unavailable")
        return set(self._all_config_ids)

    async def delete_superseded_entities(self, **kwargs: Any) -> None:
        self.superseded_calls.append(kwargs)

    async def delete_points_not_in_build(self, **kwargs: Any) -> None:
        self.points_not_in_build_calls.append(kwargs)
        if len(self.points_not_in_build_calls) <= self.fail_points_not_in_build_times:
            raise RuntimeError("qdrant sweep unavailable")


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
    channel_fn: Any = graphrag_channel,
) -> tuple[GraphRagBuilder, FakeConfigStore, FakeDb]:
    db = FakeDb(builder_group_project_id=cfg.project_id)
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
        channel_fn=channel_fn,
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
    # F-6 guard (§8.4): a delta build does NOT prune the config graph nor use the
    # build-scoped vector sweep — replacement semantics stay off by default.
    assert neo4j.applied_replace == [False]
    assert neo4j.remove_stale_calls == []
    assert vectors.points_not_in_build_calls == []


# ---------------------------------------------------------------------------
# R7.09a pre-flight — AC-5 of 2026-07-16-headless-turn-key-group-authz
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_fails_fast_when_builder_key_group_is_deleted() -> None:
    # Before this pre-flight, a deleted builder group surfaced as an empty
    # eligible-member list partway through extraction (group_repository.py's
    # join fixes that for free, but it reads as indistinguishable from
    # exhaustion). This fails loudly, before the first extraction window --
    # and, per R7.09a's "never silently", records FAILED + an audit event
    # before raising, so the rejection is visible via the config's own state.
    from contexts.knowledge.domain.errors import GraphRagBuilderKeyGroupProjectMismatch

    cfg = _make_cfg()
    neo4j, vectors = FakeNeo4j(), FakeVectorStore()
    lock, snaps = FakeLock(), FakeSnapshots()
    extractor = FakeExtractor(_make_triples())
    # The group is missing entirely (deleted): the fake's lookup returns nothing.
    builder, store, db = _make_builder(
        cfg=cfg,
        neo4j=neo4j,
        vectors=vectors,
        lock=lock,
        snapshots=snaps,
        extractor=extractor,
    )
    db.builder_group_project_id = None

    with pytest.raises(GraphRagBuilderKeyGroupProjectMismatch):
        await builder.run(config_id=cfg.id, mode="delta", triggered_by="manual")

    # Recorded FAILED with an actionable error -- not left at IDLE -- and audited.
    assert [t[0] for t in store.transitions] == [BuildState.FAILED]
    assert str(cfg.builder_key_group_id) in (store.transitions[0][1] or "")
    assert db.committed is True
    # No extraction and no Neo4j write were ever attempted.
    assert extractor.calls == 0
    assert neo4j.applied == []


@pytest.mark.asyncio
async def test_build_fails_fast_when_builder_key_group_is_cross_project() -> None:
    from contexts.knowledge.domain.errors import GraphRagBuilderKeyGroupProjectMismatch

    cfg = _make_cfg()
    neo4j, vectors = FakeNeo4j(), FakeVectorStore()
    lock, snaps = FakeLock(), FakeSnapshots()
    extractor = FakeExtractor(_make_triples())
    # The group exists and is live, but belongs to a different project.
    builder, store, db = _make_builder(
        cfg=cfg,
        neo4j=neo4j,
        vectors=vectors,
        lock=lock,
        snapshots=snaps,
        extractor=extractor,
    )
    db.builder_group_project_id = uuid.uuid4()

    with pytest.raises(GraphRagBuilderKeyGroupProjectMismatch):
        await builder.run(config_id=cfg.id, mode="delta", triggered_by="manual")

    assert [t[0] for t in store.transitions] == [BuildState.FAILED]
    assert db.committed is True
    assert extractor.calls == 0


@pytest.mark.asyncio
async def test_replace_build_prunes_graph_and_uses_build_scoped_vector_sweep() -> None:
    # F-6: a full-corpus knowmap replacement build applies replacement semantics —
    # apply_triples(replace=True), a stale-relation/entity removal pass, and the
    # build-scoped Qdrant sweep (not the name-scoped supersede).
    cfg = _make_cfg()
    neo4j, vectors = FakeNeo4j(), FakeVectorStore()
    lock, snaps = FakeLock(), FakeSnapshots()
    extractor = FakeExtractor(_make_triples())
    builder, _store, _db = _make_builder(
        cfg=cfg,
        neo4j=neo4j,
        vectors=vectors,
        lock=lock,
        snapshots=snaps,
        extractor=extractor,
    )

    result = await builder.run(config_id=cfg.id, mode="delta", triggered_by="manual", replace=True)

    assert result.state is BuildState.IDLE
    # apply_triples carried the replacement flag, and the removal pass ran for this
    # build inside Phase 1.
    assert neo4j.applied_replace == [True]
    assert neo4j.remove_stale_calls == [result.build_id]
    # The build-scoped vector sweep ran (a superset of the name-scoped supersede);
    # the name-scoped supersede did NOT.
    assert vectors.superseded_calls == []
    assert len(vectors.points_not_in_build_calls) == 1
    sweep = vectors.points_not_in_build_calls[0]
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
        FakeDb(builder_group_project_id=cfg.project_id),  # type: ignore[arg-type]
        neo4j=neo4j,
        vector_store=FakeVectorStore(),  # type: ignore[arg-type]
        extractor=FakeExtractor(triples),
        lock_store=FakeLock(),
        snapshot_store=FakeSnapshots(),
        delta_loader=FakeWindowLoader([window]),
        embedder_factory=_embedder_factory,
        configs=FakeConfigStore(cfg),  # type: ignore[arg-type]
        channel_fn=graphrag_channel,
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
    db = FakeDb(builder_group_project_id=cfg.project_id)
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
        channel_fn=graphrag_channel,
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
        channel_fn=graphrag_channel,
    )
    # The reconciler resolves its repo via the injected factory; point it at
    # the fake store (which implements list_in_state/set_state). Stub commit/close.
    store.commit = _noop  # type: ignore[attr-defined]
    store.close = _noop  # type: ignore[attr-defined]

    touched = await recon.run_once()
    assert touched == [cfg.id]
    assert store.cfg.last_build_state is BuildState.IDLE  # type: ignore[comparison-overlap]
    assert store.cfg.last_build_at is not None  # type: ignore[unreachable]


async def _run_recovery_loop(*, replace_on_recovery: bool) -> FakeVectorStore:
    """Drive a config into FAILED_COMPENSATING then heal it via a reconciler
    whose phase-2 succeeds first try, returning the vector store so the caller can
    assert whether the build-scoped replace sweep ran."""
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

    async def phase2(*, cfg, build_id) -> None:
        return None  # succeeds first try

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
        channel_fn=graphrag_channel,
        replace_on_recovery=replace_on_recovery,
    )
    store.commit = _noop  # type: ignore[attr-defined]
    store.close = _noop  # type: ignore[attr-defined]

    touched = await recon.run_once()
    assert touched == [cfg.id]
    assert store.cfg.last_build_state is BuildState.IDLE  # type: ignore[comparison-overlap]
    return vectors


@pytest.mark.asyncio
async def test_reconciler_recovery_runs_replace_sweep_for_knowmap() -> None:
    # Finding 2: a Knowledge Map (replace=True) build whose phase-2 failed and is
    # recovered here must run the build-scoped ``delete_points_not_in_build`` sweep
    # — the original build's own sweep never ran, so without this the old-build
    # Qdrant points leak and retrieval surfaces entities the current corpus no
    # longer produces (they have no backing Neo4j edges after the phase-1 prune).
    vectors = await _run_recovery_loop(replace_on_recovery=True)

    assert len(vectors.points_not_in_build_calls) == 1
    call = vectors.points_not_in_build_calls[0]
    assert call["config_id"] is not None
    assert call["keep_build_id"] is not None
    # DOM-8's name-scoped supersede is for delta builds and must NOT run here.
    assert vectors.superseded_calls == []


@pytest.mark.asyncio
async def test_reconciler_recovery_skips_replace_sweep_for_concept_map() -> None:
    # The Concept Map (delta) loop must keep the DOM-8 no-sweep behavior: its builds
    # accumulate across deltas, so a blanket build-scoped delete would wipe live
    # entities from earlier builds. Default replace_on_recovery=False.
    vectors = await _run_recovery_loop(replace_on_recovery=False)

    assert vectors.points_not_in_build_calls == []
    assert vectors.superseded_calls == []


async def _recover_with_failing_sweep(*, fail_sweep_times: int) -> tuple[FakeVectorStore, Any]:
    """Drive a config into FAILED_COMPENSATING then heal it via a replace-on-recovery
    reconciler whose phase-2 always succeeds but whose build-scoped sweep fails its
    first ``fail_sweep_times`` calls. Returns (vectors, store) so the caller can assert
    both the retry count and the final build state."""
    cfg = _make_cfg()
    neo4j = FakeNeo4j()
    vectors = FakeVectorStore(
        raise_on_upsert=RuntimeError("qdrant down"),
        fail_points_not_in_build_times=fail_sweep_times,
    )
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

    async def phase2(*, cfg, build_id) -> None:
        return None  # phase-2 itself always succeeds; only the sweep fails

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
        channel_fn=graphrag_channel,
        replace_on_recovery=True,
    )
    store.commit = _noop  # type: ignore[attr-defined]
    store.close = _noop  # type: ignore[attr-defined]

    await recon.run_once()
    return vectors, store


@pytest.mark.asyncio
async def test_reconciler_recovery_retries_when_replace_sweep_fails_transiently() -> None:
    # Finding 2 (folded into the phase-2 retry unit): a transient Qdrant failure in
    # the build-scoped sweep must retry the whole recovery step, not be swallowed.
    # First sweep call raises, the second (next attempt) succeeds → the build still
    # reaches IDLE and the sweep is proven to have re-run.
    vectors, store = await _recover_with_failing_sweep(fail_sweep_times=1)

    assert store.cfg.last_build_state is BuildState.IDLE  # type: ignore[comparison-overlap]
    assert len(vectors.points_not_in_build_calls) == 2  # type: ignore[unreachable]


@pytest.mark.asyncio
async def test_reconciler_persistent_replace_sweep_failure_rolls_back_not_idle() -> None:
    # Finding 2: before the fold, a sweep failure was caught best-effort and the build
    # was finalised IDLE anyway — leaking the old-build points with no recovery path.
    # Now a sweep that fails on every attempt exhausts the retry loop and rolls back
    # to FAILED (compensation runs) instead of advertising a clean IDLE over a leak.
    vectors, store = await _recover_with_failing_sweep(fail_sweep_times=99)

    assert store.cfg.last_build_state is BuildState.FAILED
    # The sweep was attempted once per retry (never silently skipped or swallowed).
    assert len(vectors.points_not_in_build_calls) == len(RETRY_BACKOFF_S)


@pytest.mark.asyncio
async def test_reconciler_audits_with_injected_resource_type() -> None:
    # Regression (code review finding): a loop constructed with
    # repo_factory=KnowmapConfigRepository (the _knowmap_loop() in
    # app/workers/graphrag_reconciler.py) must stamp its audit events with
    # resource_type="knowmap_config", not the hardcoded "graphrag_config" --
    # otherwise a healed knowmap config's audit row mislabels its own id as
    # a graphrag_config resource.
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

    async def phase2(*, cfg, build_id) -> None:
        return None  # succeeds first try

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
        channel_fn=graphrag_channel,
        resource_type="knowmap_config",
    )
    store.commit = _noop  # type: ignore[attr-defined]
    store.close = _noop  # type: ignore[attr-defined]

    await recon.run_once()

    audit_insert = store.executed[-1]
    params = audit_insert.compile().params
    assert params["resource_type"] == "knowmap_config"
    assert params["action"] == "knowmap.reconciled"


@pytest.mark.asyncio
async def test_reconciler_recovery_publishes_to_overridden_channel(monkeypatch: Any) -> None:
    # AC-4: a Knowledge Map's reconciler-recovered build (the IDLE-with-build_id
    # recovery-success path) must publish onto its own channel_fn, not the
    # graphrag_channel default — mirrors the builder's channel_fn (AC-3).
    channels: list[Any] = []

    async def _capture(config_id: Any, state: str, *, channel: Any = "unset", **_kw: Any) -> None:
        channels.append((state, channel))

    from contexts.knowledge.application import graphrag_reconciler as rmod

    monkeypatch.setattr(rmod, "publish_build_state", _capture)

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

    async def phase2(*, cfg, build_id) -> None:
        return None  # succeeds first try

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
        channel_fn=lambda config_id: f"ws:knowmap:{config_id}",
    )
    store.commit = _noop  # type: ignore[attr-defined]
    store.close = _noop  # type: ignore[attr-defined]

    touched = await recon.run_once()

    assert touched == [cfg.id]
    idle_channels = [c for state, c in channels if state == BuildState.IDLE.value]
    assert idle_channels, "expected an IDLE publish on recovery"
    assert all(c == f"ws:knowmap:{cfg.id}" for c in idle_channels)


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
        channel_fn=graphrag_channel,
    )
    store.commit = _noop  # type: ignore[attr-defined]
    store.close = _noop  # type: ignore[attr-defined]

    await recon.run_once()
    assert store.cfg.last_build_state is BuildState.FAILED
    # Rollback was attempted.
    assert neo4j.deleted  # delete_by_build called


# ---------------------------------------------------------------------------
# F-7 — a failed compensation must not be advertised as a clean rollback.
# ---------------------------------------------------------------------------


def _recon_over(
    *,
    store: FakeConfigStore,
    neo4j: FakeNeo4j,
    vectors: FakeVectorStore,
    snaps: FakeSnapshots,
    phase2: Any,
    lock: FakeLock | None = None,
) -> ReconciliationLoop:
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
        lock_store=lock,
        channel_fn=graphrag_channel,
    )
    store.commit = _noop  # type: ignore[attr-defined]
    store.close = _noop  # type: ignore[attr-defined]
    return recon


async def _always_fails_phase2(*, cfg: Any, build_id: Any) -> None:
    raise RuntimeError("qdrant still down")


@pytest.mark.asyncio
async def test_failed_compensation_retains_recovery_material(monkeypatch: Any) -> None:
    # F-7 §8.1 / AC-1, AC-2, AC-4 (primary red-first): when the Neo4j restore throws
    # during compensation, the config must stay FAILED_COMPENSATING, keep its snapshot
    # and current-build pointer, audit outcome=compensation_failed (never rolled_back),
    # and be re-selectable by the next sweep. Before the fix _rollback unconditionally
    # marked FAILED, deleted the snapshot, and audited rolled_back.
    from contexts.knowledge.application import graphrag_reconciler as recon_mod

    events: list[Any] = []

    async def _cap(_db: Any, event: Any) -> None:
        events.append(event)

    monkeypatch.setattr(recon_mod.audit, "emit", _cap)

    cfg = _make_cfg()
    neo4j = FakeNeo4j(raise_on_restore=RuntimeError("neo4j down during compensation"))
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
    assert snaps.store  # snapshot present pre-compensation
    assert snaps.current  # current pointer present pre-compensation

    recon = _recon_over(store=store, neo4j=neo4j, vectors=vectors, snaps=snaps, phase2=_always_fails_phase2)
    await recon.run_once()

    # Compensation failed → the config stays mid-compensation (its FAILED_COMPENSATING
    # state is durably committed so the next sweep re-attempts it), never healed to IDLE.
    assert store.cfg.last_build_state is BuildState.FAILED_COMPENSATING
    # Recovery material is retained for the next attempt.
    assert snaps.store, "snapshot must be retained on failed compensation"
    assert snaps.current, "current-build pointer must be retained on failed compensation"
    # The audit records a distinct, alertable outcome — never a false rolled_back.
    outcomes = [e.metadata.get("outcome") for e in events]
    assert "compensation_failed" in outcomes
    assert "rolled_back" not in outcomes
    # AC-4: still enrolled in the heal loop.
    assert await store.list_in_state(BuildState.FAILED_COMPENSATING) == [store.cfg]


@pytest.mark.asyncio
async def test_successful_compensation_finalizes_and_audits_rolled_back(monkeypatch: Any) -> None:
    # F-7 §8.2 / AC-3 (guard): a compensation whose Neo4j restore succeeds keeps the
    # prior behavior — terminal FAILED, snapshot deleted, pointer cleared, rolled_back.
    from contexts.knowledge.application import graphrag_reconciler as recon_mod

    events: list[Any] = []

    async def _cap(_db: Any, event: Any) -> None:
        events.append(event)

    monkeypatch.setattr(recon_mod.audit, "emit", _cap)

    cfg = _make_cfg()
    neo4j = FakeNeo4j()  # restore succeeds
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

    recon = _recon_over(store=store, neo4j=neo4j, vectors=vectors, snaps=snaps, phase2=_always_fails_phase2)
    await recon.run_once()

    assert store.cfg.last_build_state is BuildState.FAILED
    assert not snaps.store, "snapshot deleted on successful rollback"
    assert not snaps.current, "pointer cleared on successful rollback"
    assert neo4j.restored, "restore_from_snapshot ran"
    outcomes = [e.metadata.get("outcome") for e in events]
    assert "rolled_back" in outcomes
    assert "compensation_failed" not in outcomes


@pytest.mark.asyncio
async def test_no_snapshot_compensation_audits_unavailable_not_rolled_back(monkeypatch: Any) -> None:
    # F-7 §8 / AC-5: the no-snapshot compensation path must emit a distinct
    # compensation_unavailable outcome — never rolled_back, so dashboards never count it
    # as a clean rollback — and must land in RECOVERY_UNAVAILABLE, not ordinary FAILED
    # (2026-07-17-graphrag-reset-expired-recovery AC-5). Ordinary FAILED is readable and
    # re-swept; this config's partial Neo4j state can never be rolled back, so serving it
    # or retrying compensation would both be wrong.
    from contexts.knowledge.application import graphrag_reconciler as recon_mod

    events: list[Any] = []

    async def _cap(_db: Any, event: Any) -> None:
        events.append(event)

    monkeypatch.setattr(recon_mod.audit, "emit", _cap)

    import dataclasses

    cfg = dataclasses.replace(_make_cfg(), last_build_state=BuildState.FAILED_COMPENSATING)
    neo4j = FakeNeo4j()
    vectors = FakeVectorStore()
    snaps = FakeSnapshots()  # empty — no snapshot, no current pointer
    store = FakeConfigStore(cfg)

    recon = _recon_over(store=store, neo4j=neo4j, vectors=vectors, snaps=snaps, phase2=_always_fails_phase2)
    await recon.run_once()

    assert store.cfg.last_build_state is BuildState.RECOVERY_UNAVAILABLE
    outcomes = [e.metadata.get("outcome") for e in events]
    assert "compensation_unavailable" in outcomes
    assert "rolled_back" not in outcomes

    # Terminal: a second sweep must not pick it up again. _STUCK_STATES is matched by
    # exact equality, so the new state is simply never selected.
    events.clear()
    await recon.run_once()
    assert store.cfg.last_build_state is BuildState.RECOVERY_UNAVAILABLE
    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "accepted"),
    [
        (BuildState.IDLE, True),
        (BuildState.FAILED, True),
        # AC-9: the manual/engine escape from an irrecoverable config.
        (BuildState.RECOVERY_UNAVAILABLE, True),
        (BuildState.RUNNING, False),
        (BuildState.NEO4J_COMMITTED, False),
        (BuildState.FAILED_COMPENSATING, False),
    ],
)
async def test_engine_gate_admits_recovery_unavailable(state: BuildState, accepted: bool) -> None:
    """A rebuild is the only way out of RECOVERY_UNAVAILABLE, so the engine must take it.

    The state is outside the reconciler's sweep set and outside the auto-trigger
    whitelist by design, so if this gate refused it too the config would be wedged with
    no escape at all (2026-07-17-graphrag-reset-expired-recovery Q-5).
    """
    import dataclasses

    from contexts.knowledge.domain.errors import GraphRagBuildBusy

    cfg = dataclasses.replace(_make_cfg(), last_build_state=state)
    builder, store, _db = _make_builder(
        cfg=cfg,
        neo4j=FakeNeo4j(),
        vectors=FakeVectorStore(),
        lock=FakeLock(),
        snapshots=FakeSnapshots(),
        extractor=FakeExtractor(_make_triples()),
    )

    if accepted:
        await builder.run(config_id=cfg.id)
        # A completed build settles back on IDLE; the point for RECOVERY_UNAVAILABLE is
        # that the config is no longer stuck there.
        assert store.cfg.last_build_state is BuildState.IDLE
    else:
        with pytest.raises(GraphRagBuildBusy):
            await builder.run(config_id=cfg.id)
        assert store.cfg.last_build_state is state, "a refused build must not touch the state"


def test_auto_triggers_refuse_recovery_unavailable() -> None:
    """Automatic triggers must never rebuild over known-inconsistent data (AC-9).

    Deliberately narrower than the engine gate above: a message-count or silence timer
    firing a rebuild would paper over the inconsistency without anyone deciding to.
    """
    from contexts.knowledge.application.graphrag_triggers import _BUILDABLE_STATES

    assert BuildState.RECOVERY_UNAVAILABLE not in _BUILDABLE_STATES
    assert frozenset({BuildState.IDLE, BuildState.FAILED}) == _BUILDABLE_STATES


def test_recovery_unavailable_is_read_blocked_but_not_swept() -> None:
    """The two membership sets have deliberately diverged (AC-4 / AC-9).

    They were identical before this change and both carry comments saying so; this
    pins the divergence so a future edit to one cannot silently restore the coupling.
    """
    from contexts.knowledge.application.graphrag_reconciler import _STUCK_STATES
    from contexts.knowledge.domain.graphrag import IN_FLIGHT_BUILD_STATES

    assert BuildState.RECOVERY_UNAVAILABLE in IN_FLIGHT_BUILD_STATES
    assert BuildState.RECOVERY_UNAVAILABLE not in _STUCK_STATES
    assert set(_STUCK_STATES) < IN_FLIGHT_BUILD_STATES  # strict subset
    # Ordinary FAILED stays readable and rebuildable.
    assert BuildState.FAILED not in IN_FLIGHT_BUILD_STATES


@pytest.mark.asyncio
async def test_failed_compensation_of_running_crash_stays_running(monkeypatch: Any) -> None:
    # F-7 regression: _rollback is shared with the crashed-Phase-1 RUNNING path. A
    # failed compensation must keep a RUNNING crash in RUNNING (re-swept as a rollback),
    # NOT flip it to FAILED_COMPENSATING — which would reroute it into the Phase-2 retry
    # loop and complete a build the RUNNING path deliberately rolls back.
    import dataclasses

    from contexts.knowledge.application import graphrag_reconciler as recon_mod

    events: list[Any] = []

    async def _cap(_db: Any, event: Any) -> None:
        events.append(event)

    monkeypatch.setattr(recon_mod.audit, "emit", _cap)

    cfg = dataclasses.replace(_make_cfg(), last_build_state=BuildState.RUNNING)
    build_id = uuid.uuid4()
    neo4j = FakeNeo4j(raise_on_restore=RuntimeError("neo4j down during compensation"))
    snaps = FakeSnapshots()
    snaps.store[(cfg.id, build_id)] = {"edges": []}
    snaps.current[cfg.id] = build_id
    store = FakeConfigStore(cfg)

    async def phase2_must_not_run(*, cfg: Any, build_id: Any) -> None:  # pragma: no cover
        raise AssertionError("a RUNNING crash must roll back, never run Phase-2")

    recon = _recon_over(
        store=store,
        neo4j=neo4j,
        vectors=FakeVectorStore(),
        snaps=snaps,
        phase2=phase2_must_not_run,
        lock=FakeLock(),
    )
    await recon.run_once()

    # State preserved as RUNNING so the next sweep rolls back again (not Phase-2).
    assert store.cfg.last_build_state is BuildState.RUNNING
    assert snaps.store, "snapshot retained for the next rollback attempt"
    assert snaps.current, "current pointer retained"
    outcomes = [e.metadata.get("outcome") for e in events]
    assert outcomes == ["compensation_failed"]


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
        sweep_consumers=[
            GraphConsumer(
                repo_factory=lambda _db: store,  # type: ignore[arg-type, return-value]
                vector_store=vectors,  # type: ignore[arg-type]
                resource_type="graphrag_config",
            )
        ],
        channel_fn=graphrag_channel,
    )

    touched = await recon.run_once()

    # No stuck configs → nothing healed; the sweep is the only work this cycle.
    assert touched == []
    # The sweep diffs orphans against LIVE (include_deleted=False) ids, then loads
    # include_deleted=True ids to attribute each orphan to its owning consumer.
    assert store.list_all_ids_calls == [False, True]
    # Both orphans are purged from Neo4j; the live config's subgraph is untouched.
    assert set(neo4j.deleted_all) == {orphan_id, legacy_orphan}
    assert cfg.id not in neo4j.deleted_all
    # Only the project-tagged orphan is Qdrant-sweepable; the legacy one is skipped.
    assert [d["config_id"] for d in vectors.deleted_by_config] == [orphan_id]
    assert vectors.deleted_by_config[0]["project_id"] == orphan_project


@pytest.mark.asyncio
async def test_orphan_sweep_attributes_to_owning_consumer(monkeypatch: Any) -> None:
    # A single consumer-aware sweep across two consumers sharing the Neo4j node
    # space: a live graphrag config is protected; a soft-deleted knowmap config`s
    # orphaned subgraph is swept, audited as resource_type=knowmap_config, and its
    # points purged from the knowmap collection (with the graphrag collection swept
    # as a harmless no-op so nothing leaks).
    live_a, proj_a = uuid.uuid4(), uuid.uuid4()  # live graphrag config
    orphan_b, proj_b = uuid.uuid4(), uuid.uuid4()  # soft-deleted knowmap orphan

    neo4j = FakeNeo4j(config_ids=[(live_a, proj_a), (orphan_b, proj_b)])
    gr_vectors, km_vectors = FakeVectorStore(), FakeVectorStore()

    class _IdStore:
        def __init__(self, *, live: set[uuid.UUID], all_ids: set[uuid.UUID]) -> None:
            self._live, self._all = live, all_ids

        async def list_in_state(self, _state: BuildState) -> list[Any]:
            return []

        async def list_all_ids(self, *, include_deleted: bool = False) -> set[uuid.UUID]:
            return set(self._all if include_deleted else self._live)

    gr_store = _IdStore(live={live_a}, all_ids={live_a})
    km_store = _IdStore(live=set(), all_ids={orphan_b})  # deleted_at set → not live

    class _Db:
        async def commit(self) -> None: ...
        async def rollback(self) -> None: ...
        async def close(self) -> None: ...

    events: list[Any] = []

    async def _capture(_db: Any, event: Any) -> None:
        events.append(event)

    from contexts.knowledge.application import graphrag_reconciler as recon_mod

    monkeypatch.setattr(recon_mod.audit, "emit", _capture)

    async def never_phase2(*, cfg: Any, build_id: Any) -> None:  # pragma: no cover
        raise AssertionError("no stuck configs")

    recon = ReconciliationLoop(
        session_factory=lambda: _Db(),  # type: ignore[arg-type, return-value]
        repo_factory=lambda _db: gr_store,  # type: ignore[arg-type, return-value]
        neo4j=neo4j,
        vector_store=gr_vectors,  # type: ignore[arg-type]
        snapshot_store=FakeSnapshots(),
        phase2_retry=never_phase2,
        sleeper=_noop,
        sweep_consumers=[
            GraphConsumer(
                repo_factory=lambda _db: gr_store,  # type: ignore[arg-type, return-value]
                vector_store=gr_vectors,  # type: ignore[arg-type]
                resource_type="graphrag_config",
            ),
            GraphConsumer(
                repo_factory=lambda _db: km_store,  # type: ignore[arg-type, return-value]
                vector_store=km_vectors,  # type: ignore[arg-type]
                resource_type="knowmap_config",
            ),
        ],
        channel_fn=graphrag_channel,
    )

    touched = await recon.run_once()

    assert touched == []
    # Only the knowmap orphan is swept; the live graphrag config is untouched.
    assert neo4j.deleted_all == [orphan_b]
    assert len(events) == 1
    event = events[0]
    assert event.resource_type == "knowmap_config"  # attributed to the owning table
    assert event.resource_id == orphan_b
    assert event.metadata["vectors_purged"] == {"knowmap_config": True, "graphrag_config": True}
    # Purged from both collections (owner + no-op) so nothing leaks.
    assert [d["config_id"] for d in km_vectors.deleted_by_config] == [orphan_b]
    assert [d["config_id"] for d in gr_vectors.deleted_by_config] == [orphan_b]


# ---------------------------------------------------------------------------
# F-8 — the orphan sweep discovers candidates from Qdrant too, not just Neo4j.
# ---------------------------------------------------------------------------


def _sweep_recon(*, store: FakeConfigStore, neo4j: FakeNeo4j, vectors: FakeVectorStore) -> ReconciliationLoop:
    async def never_phase2(*, cfg: Any, build_id: Any) -> None:  # pragma: no cover
        raise AssertionError("no stuck configs in these sweep tests")

    recon = ReconciliationLoop(
        session_factory=lambda: store,  # type: ignore[arg-type, return-value]
        repo_factory=lambda _db: store,  # type: ignore[arg-type, return-value]
        neo4j=neo4j,
        vector_store=vectors,  # type: ignore[arg-type]
        snapshot_store=FakeSnapshots(),
        phase2_retry=never_phase2,
        sleeper=_noop,
        sweep_consumers=[
            GraphConsumer(
                repo_factory=lambda _db: store,  # type: ignore[arg-type, return-value]
                vector_store=vectors,  # type: ignore[arg-type]
                resource_type="graphrag_config",
            )
        ],
        channel_fn=graphrag_channel,
    )
    store.commit = _noop  # type: ignore[attr-defined]
    store.close = _noop  # type: ignore[attr-defined]
    return recon


@pytest.mark.asyncio
async def test_orphan_sweep_discovers_qdrant_only_orphan() -> None:
    # F-8 §8.1 / AC-1, AC-2 (primary red-first): a config whose Neo4j subgraph is
    # gone but whose Qdrant points survive (a Qdrant-only orphan from a partial
    # teardown) must be discovered via Qdrant enumeration and purged. Before the fix
    # the candidate set was Neo4j-only, so nothing was swept.
    cfg = _make_cfg()  # live config
    qdrant_orphan, orphan_project = uuid.uuid4(), uuid.uuid4()
    neo4j = FakeNeo4j(config_ids=[(cfg.id, cfg.project_id)])  # no row for the orphan
    vectors = FakeVectorStore(all_config_ids={(qdrant_orphan, orphan_project)})
    store = FakeConfigStore(cfg)

    touched = await _sweep_recon(store=store, neo4j=neo4j, vectors=vectors).run_once()

    assert touched == []
    # The Qdrant-only orphan is purged from Qdrant (its config-scoped points)...
    assert [d["config_id"] for d in vectors.deleted_by_config] == [qdrant_orphan]
    assert vectors.deleted_by_config[0]["project_id"] == orphan_project
    # ...and its (already-gone) Neo4j subgraph is purged idempotently.
    assert qdrant_orphan in neo4j.deleted_all
    # The live config is never touched.
    assert cfg.id not in neo4j.deleted_all


@pytest.mark.asyncio
async def test_orphan_sweep_does_not_purge_live_config_from_qdrant_enum() -> None:
    # F-8 §8.2 / AC-3 (guard): a config present in the Qdrant enumeration but still
    # live in Postgres is never purged — the Postgres live set stays authoritative.
    cfg = _make_cfg()  # live
    neo4j = FakeNeo4j(config_ids=[])  # Neo4j enumerates nothing
    vectors = FakeVectorStore(all_config_ids={(cfg.id, cfg.project_id)})
    store = FakeConfigStore(cfg)

    await _sweep_recon(store=store, neo4j=neo4j, vectors=vectors).run_once()

    assert vectors.deleted_by_config == []
    assert neo4j.deleted_all == []


@pytest.mark.asyncio
async def test_orphan_sweep_degrades_when_qdrant_enumeration_fails() -> None:
    # F-8 §8.3 / AC-4 (guard): a Qdrant enumeration failure logs and falls back to
    # the Neo4j-sourced candidates without aborting the sweep.
    cfg = _make_cfg()  # live
    neo4j_orphan, orphan_project = uuid.uuid4(), uuid.uuid4()
    neo4j = FakeNeo4j(config_ids=[(cfg.id, cfg.project_id), (neo4j_orphan, orphan_project)])
    vectors = FakeVectorStore(raise_on_list_config_ids=True)
    store = FakeConfigStore(cfg)

    # Must not raise despite the Qdrant enumeration failing.
    await _sweep_recon(store=store, neo4j=neo4j, vectors=vectors).run_once()

    # The Neo4j-sourced orphan is still swept.
    assert neo4j_orphan in neo4j.deleted_all
    assert [d["config_id"] for d in vectors.deleted_by_config] == [neo4j_orphan]
    assert cfg.id not in neo4j.deleted_all


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


@pytest.mark.asyncio
async def test_construction_requires_channel_fn() -> None:
    # Regression guard (code review, 2026-07-10): channel_fn used to default to
    # None, silently falling back to graphrag_channel inside publish_build_state
    # — a future third consumer of this shared engine that forgot to wire its
    # own channel_fn would misdirect its build-state events with no error.
    # channel_fn is now a required keyword-only argument, so that mistake is a
    # TypeError at construction time instead of a silent cross-wire.
    cfg = _make_cfg()
    db = FakeDb()
    store = FakeConfigStore(cfg)
    with pytest.raises(TypeError, match="channel_fn"):
        GraphRagBuilder(
            db,  # type: ignore[arg-type]
            neo4j=FakeNeo4j(),
            vector_store=FakeVectorStore(),  # type: ignore[arg-type]
            extractor=FakeExtractor(_make_triples()),
            lock_store=FakeLock(),
            snapshot_store=FakeSnapshots(),
            delta_loader=FakeDeltaLoader(),
            embedder_factory=_embedder_factory,
            configs=store,  # type: ignore[arg-type]
        )  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_default_test_channel_fn_matches_production_concept_map_wiring(monkeypatch: Any) -> None:
    # AC-5 regression guard: a Concept Map builder must publish onto
    # graphrag_channel(config_id), matching app/workers/tasks/graphrag.py's
    # explicit channel_fn=graphrag_channel wiring.
    channels: list[Any] = []

    async def _capture(config_id: Any, state: str, *, channel: Any, **_kw: Any) -> None:
        channels.append(channel)

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
        channel_fn=graphrag_channel,
    )

    await builder.run(config_id=cfg.id)

    assert channels, "expected at least one publish_build_state call"
    assert all(c == graphrag_channel(cfg.id) for c in channels)


@pytest.mark.asyncio
async def test_channel_override_used_when_channel_fn_set(monkeypatch: Any) -> None:
    # A Knowledge Map builder passes channel_fn=knowmap_channel so build-state
    # publishes land on ws:knowmap:{id}, not ws:graphrag:{id} (Phase 3β).
    channels: list[Any] = []

    async def _capture(config_id: Any, state: str, *, channel: Any = "unset", **_kw: Any) -> None:
        channels.append(channel)

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
        channel_fn=lambda config_id: f"ws:knowmap:{config_id}",
    )

    await builder.run(config_id=cfg.id)

    assert channels, "expected at least one publish_build_state call"
    assert all(c == f"ws:knowmap:{cfg.id}" for c in channels)


# ---------------------------------------------------------------------------
# F-9 — deterministic Qdrant point ids make Phase-2 retries idempotent.
# ---------------------------------------------------------------------------


class DictVectorStore:
    """Vector store backed by a dict keyed on point id, so a re-upsert of the
    same id overwrites rather than duplicating — the exact Qdrant semantics F-9
    relies on."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, str]] = {}

    async def ensure_graphrag_collection(self, project_id, *, vector_size, **_: Any) -> None:
        return None

    async def upsert_entities(self, *, project_id, config_id, build_id, points) -> None:
        for pid, _vec, entity, _desc in points:
            self.points[str(pid)] = {"entity": entity, "build_id": str(build_id)}


def test_deterministic_point_id_is_stable_and_varies() -> None:
    # §8.1 / AC-3: stable for equal inputs, distinct when any of config/build/entity differ.
    from contexts.knowledge.domain.graphrag import deterministic_point_id

    c, b = uuid.uuid4(), uuid.uuid4()
    assert deterministic_point_id(c, b, "alice") == deterministic_point_id(c, b, "alice")
    assert deterministic_point_id(c, b, "alice") != deterministic_point_id(c, b, "bob")
    assert deterministic_point_id(c, b, "alice") != deterministic_point_id(uuid.uuid4(), b, "alice")
    assert deterministic_point_id(c, b, "alice") != deterministic_point_id(c, uuid.uuid4(), "alice")


@pytest.mark.asyncio
async def test_builder_upserts_deterministic_point_ids() -> None:
    # §8.3 / AC-4: the builder mints each point id via deterministic_point_id, so a
    # partially-committed original upsert is overwritten on retry, not duplicated.
    from contexts.knowledge.domain.graphrag import deterministic_point_id

    cfg = _make_cfg()
    neo4j, vectors = FakeNeo4j(), FakeVectorStore()
    builder, _store, _db = _make_builder(
        cfg=cfg,
        neo4j=neo4j,
        vectors=vectors,
        lock=FakeLock(),
        snapshots=FakeSnapshots(),
        extractor=FakeExtractor(_make_triples()),
    )

    result = await builder.run(config_id=cfg.id)

    assert len(vectors.upserts) == 1
    points = vectors.upserts[0]
    assert points, "expected at least one upserted point"
    for pid, _vec, entity, _desc in points:
        assert pid == deterministic_point_id(cfg.id, result.build_id, entity)


@pytest.mark.asyncio
async def test_reconciler_phase2_retry_is_idempotent_on_point_id(monkeypatch: Any) -> None:
    # §8.2 / AC-1 (primary red-first): re-running the reconciler's Phase-2 retry for
    # the same (config, build) overwrites in place. Two entities → two points, never
    # four. Fails before the fix — the retry minted a fresh uuid4() per attempt.
    from app.workers import graphrag_reconciler as wmod

    cfg = _make_cfg()
    build_id = uuid.uuid4()
    neo4j = FakeNeo4j(
        triples_for_build=[{"subject": "X", "relation": "knows", "object": "Y"}],
    )
    store = DictVectorStore()
    retry = wmod._make_phase2_retry(neo4j, store)  # type: ignore[arg-type]

    class _FakeEmbedder:
        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.2, 0.3] for _ in texts]

    class _FakeDbCtx:
        async def __aenter__(self) -> _FakeDbCtx:
            return self

        async def __aexit__(self, *_a: Any) -> bool:
            return False

        async def commit(self) -> None:
            return None

    async def _resolve(_db: Any, _cfg: Any) -> tuple[str, str, uuid.UUID]:
        return "openai", "text-embedding-3-small", uuid.uuid4()

    monkeypatch.setattr(wmod, "resolve_pinned_embed_key", _resolve)
    monkeypatch.setattr(wmod, "get_sessionmaker", lambda: (lambda: _FakeDbCtx()))
    monkeypatch.setattr("contexts.keys.infrastructure.adapters.build_router", lambda _db: object())
    monkeypatch.setattr(
        "contexts.knowledge.infrastructure.embedders.router_embedder_for",
        lambda **_kw: _FakeEmbedder(),
    )

    await retry(cfg=cfg, build_id=build_id)
    await retry(cfg=cfg, build_id=build_id)

    # X + Y — one point each, not four.
    assert len(store.points) == 2
    assert {p["entity"] for p in store.points.values()} == {"X", "Y"}


async def _noop(*_a: Any, **_kw: Any) -> None:
    return None


def test_retry_backoff_tuple_is_5_steps() -> None:
    assert len(RETRY_BACKOFF_S) == 5
    assert RETRY_BACKOFF_S == (1.0, 2.0, 4.0, 8.0, 16.0)
