"""F-11/F-3 — durable, race-free project embedding-dimension pins.

Covers the pin repository's conflict decision per subsystem, the File RAG
delete-then-recreate lifecycle, a non-last delete retaining the pin, the F-3
teardown contract (the pin is released only on a Qdrant-confirmed drop/absence, in
all three products), the create-path teardown retry, and the File RAG runtime
dimension guard.

``acquire_lock`` is a no-op in these fakes, so nothing here proves the advisory lock
actually serializes anything -- that needs a real Postgres and lives in
``tests/integration/test_embedding_pin_race.py``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from contexts.knowledge.application.config_service import RagConfigService
from contexts.knowledge.domain.embedding_pin import PinKind, TeardownOutcome
from contexts.knowledge.domain.errors import (
    EmbedDimensionConflict,
    GraphRagEmbedDimensionConflict,
    KnowmapEmbedDimensionConflict,
    RagCollectionDimensionMismatch,
)
from contexts.knowledge.domain.models import RagConfigDraft
from contexts.knowledge.infrastructure.embedding_pin_repository import EmbeddingPinRepository
from contexts.knowledge.infrastructure.qdrant_store import QdrantStore

# ---------------------------------------------------------------------------
# EmbeddingPinRepository.ensure — conflict decision per subsystem (§8.4, AC-3)
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakePinSession:
    """AsyncSession stand-in: advisory lock is a no-op, ``get`` returns a preset
    row, ``insert`` is recorded. Distinguishes statement kinds by type."""

    def __init__(self, existing_dim: int | None = None) -> None:
        self._existing = SimpleNamespace(dim=existing_dim) if existing_dim is not None else None
        self.insert_count = 0
        self.delete_count = 0

    def begin_nested(self) -> Any:
        session = self

        class _Savepoint:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *exc: object) -> bool:
                return False

        _ = session
        return _Savepoint()

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        from sqlalchemy.sql.dml import Delete, Insert
        from sqlalchemy.sql.selectable import Select

        if isinstance(stmt, Insert):
            self.insert_count += 1
            return _FakeResult(None)
        if isinstance(stmt, Delete):
            self.delete_count += 1
            return _FakeResult(None)
        if isinstance(stmt, Select):
            return _FakeResult(self._existing)
        # TextClause — the advisory lock.
        return _FakeResult(None)


def _conflict_factories() -> list[tuple[PinKind, type[Exception]]]:
    return [
        (PinKind.FILE_RAG, EmbedDimensionConflict),
        (PinKind.KNOWMAP, KnowmapEmbedDimensionConflict),
        (PinKind.GRAPHRAG, GraphRagEmbedDimensionConflict),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "error"), _conflict_factories())
async def test_ensure_raises_typed_conflict_per_subsystem(kind: PinKind, error: type[Exception]) -> None:
    session = _FakePinSession(existing_dim=1536)
    repo = EmbeddingPinRepository(session)  # type: ignore[arg-type]
    with pytest.raises(error):
        await repo.ensure(
            project_id=uuid.uuid4(),
            kind=kind,
            provider="gemini",
            model="text-embedding-004",
            dim=768,
            on_conflict=lambda existing, this: error(f"{existing} != {this}"),
        )
    assert session.insert_count == 0  # a conflict never inserts


@pytest.mark.asyncio
async def test_ensure_inserts_when_absent() -> None:
    session = _FakePinSession(existing_dim=None)
    repo = EmbeddingPinRepository(session)  # type: ignore[arg-type]
    await repo.ensure(
        project_id=uuid.uuid4(),
        kind=PinKind.FILE_RAG,
        provider="openai",
        model="text-embedding-3-small",
        dim=1536,
        on_conflict=lambda existing, this: EmbedDimensionConflict("unexpected"),
    )
    assert session.insert_count == 1


@pytest.mark.asyncio
async def test_ensure_matching_dim_is_noop() -> None:
    session = _FakePinSession(existing_dim=1536)
    repo = EmbeddingPinRepository(session)  # type: ignore[arg-type]
    await repo.ensure(
        project_id=uuid.uuid4(),
        kind=PinKind.FILE_RAG,
        provider="openai",
        model="text-embedding-3-small",
        dim=1536,
        on_conflict=lambda existing, this: EmbedDimensionConflict("unexpected"),
    )
    assert session.insert_count == 0  # already pinned at this dim


# ---------------------------------------------------------------------------
# File RAG delete-then-recreate lifecycle (§8.1/§8.2, AC-1/AC-2/AC-3)
# ---------------------------------------------------------------------------


class _FakePinRepo:
    """In-memory pin repo: the advisory lock is a no-op (§8 unit-fake note)."""

    def __init__(self, *, lock_free: bool = True) -> None:
        self.pins: dict[tuple[uuid.UUID, str], int] = {}
        self.cleared: list[tuple[uuid.UUID, str]] = []
        # `lock_free=False` stands in for another transaction already holding the
        # (project, kind) key — the only thing `try_acquire_lock` reports on.
        self._lock_free = lock_free

    async def acquire_lock(self, project_id: uuid.UUID, kind: PinKind) -> None:
        return None

    async def try_acquire_lock(self, project_id: uuid.UUID, kind: PinKind) -> bool:
        return self._lock_free

    async def get(self, project_id: uuid.UUID, kind: PinKind) -> Any:
        dim = self.pins.get((project_id, kind.value))
        return SimpleNamespace(dim=dim) if dim is not None else None

    async def ensure(
        self,
        *,
        project_id: uuid.UUID,
        kind: PinKind,
        provider: str,
        model: str,
        dim: int,
        on_conflict: Any,
    ) -> None:
        existing = self.pins.get((project_id, kind.value))
        if existing is None:
            self.pins[(project_id, kind.value)] = dim
            return
        if existing != dim:
            raise on_conflict(existing, dim)

    async def clear(self, *, project_id: uuid.UUID, kind: PinKind) -> None:
        self.pins.pop((project_id, kind.value), None)
        self.cleared.append((project_id, kind.value))


def _draft(provider: str, model: str) -> RagConfigDraft:
    return RagConfigDraft(
        name="cfg",
        chunk_strategy=SimpleNamespace(value="fixed"),  # only .value read in audit
        chunk_params={},
        embed_key_id=None,
        embed_provider=provider,
        embed_model=model,
        rerank_enabled=False,
        rerank_key_id=None,
        rerank_provider=None,
        rerank_model=None,
        top_k=5,
    )


def _rag_service(pins: _FakePinRepo, live: list[Any]) -> RagConfigService:
    svc = RagConfigService(db=AsyncMock())
    svc._configs = AsyncMock()
    svc._configs.list_for_project.return_value = live
    svc._pins = pins  # type: ignore[assignment]
    return svc


def _patch_qdrant(store: Any) -> Any:
    """Patch the teardown's client construction + the File RAG store.

    ``delete_collection_bounded`` owns client lifecycle and the timeout; the store is
    patched where the service imports it, so the fake's return/raise drives the
    outcome under test.
    """
    settings = SimpleNamespace(qdrant=SimpleNamespace(url="http://q", api_key="", teardown_timeout_s=10.0))
    return (
        patch(
            "contexts.knowledge.infrastructure.qdrant_teardown.AsyncQdrantClient", return_value=AsyncMock()
        ),
        patch("app.config.settings.get_settings", return_value=settings),
        patch("contexts.knowledge.infrastructure.qdrant_store.QdrantStore", return_value=store),
    )


@pytest.mark.asyncio
async def test_last_config_drops_collection_then_releases_pin() -> None:
    # AC-2/AC-4: the pin is released only after Qdrant confirms the drop.
    pid = uuid.uuid4()
    pins = _FakePinRepo()
    pins.pins[(pid, PinKind.FILE_RAG.value)] = 1536
    svc = _rag_service(pins, live=[])  # last config already soft-deleted
    store = AsyncMock()
    store.delete_collection.return_value = True
    p1, p2, p3 = _patch_qdrant(store)
    with p1, p2, p3:
        outcome = await svc.teardown_orphan_collection(project_id=pid)
    assert outcome is TeardownOutcome.DROPPED
    store.delete_collection.assert_awaited_once_with(pid)
    assert (pid, PinKind.FILE_RAG.value) not in pins.pins


@pytest.mark.asyncio
async def test_absent_collection_releases_pin() -> None:
    # AC-4: an already-absent collection is as good a confirmation as a fresh drop.
    pid = uuid.uuid4()
    pins = _FakePinRepo()
    pins.pins[(pid, PinKind.FILE_RAG.value)] = 1536
    svc = _rag_service(pins, live=[])
    store = AsyncMock()
    store.delete_collection.return_value = False  # nothing there to drop
    p1, p2, p3 = _patch_qdrant(store)
    with p1, p2, p3:
        outcome = await svc.teardown_orphan_collection(project_id=pid)
    assert outcome is TeardownOutcome.ABSENT
    assert (pid, PinKind.FILE_RAG.value) not in pins.pins


@pytest.mark.asyncio
async def test_teardown_skips_and_keeps_pin_when_config_reappeared() -> None:
    # AC-5: a concurrent create won the lock — its collection must survive, and the
    # pin it depends on must not be released.
    pid = uuid.uuid4()
    pins = _FakePinRepo()
    pins.pins[(pid, PinKind.FILE_RAG.value)] = 1536
    reappeared = SimpleNamespace(embed_provider="openai", embed_model="text-embedding-3-small")
    svc = _rag_service(pins, live=[reappeared])
    store = AsyncMock()
    p1, p2, p3 = _patch_qdrant(store)
    with p1, p2, p3:
        outcome = await svc.teardown_orphan_collection(project_id=pid)
    assert outcome is TeardownOutcome.SKIPPED_LIVE_CONFIG
    store.delete_collection.assert_not_awaited()
    assert pins.pins[(pid, PinKind.FILE_RAG.value)] == 1536


@pytest.mark.asyncio
async def test_recreate_after_drop_repins_new_dimension() -> None:
    pid = uuid.uuid4()
    pins = _FakePinRepo()  # pin cleared by a prior drop-empty
    svc = _rag_service(pins, live=[])
    created = SimpleNamespace(
        id=uuid.uuid4(),
        name="cfg",
        chunk_strategy=SimpleNamespace(value="fixed"),
        embed_provider="openai",
        embed_model="text-embedding-3-large",
        rerank_enabled=False,
    )
    svc._configs.create.return_value = created
    with patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()):
        out = await svc.create(
            project_id=pid,
            draft=_draft("openai", "text-embedding-3-large"),
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    assert out.embed_model == "text-embedding-3-large"  # create returned the new config
    assert pins.pins[(pid, PinKind.FILE_RAG.value)] == 3072  # re-pinned at the new dim


@pytest.mark.asyncio
async def test_recreate_rejected_when_pin_survives_sibling_delete() -> None:
    # AC-3: a non-last delete kept the pin; a create at a different dimension is
    # rejected through the pin repo even though the live-sibling scan is empty
    # for this (contrived) case.
    pid = uuid.uuid4()
    pins = _FakePinRepo()
    pins.pins[(pid, PinKind.FILE_RAG.value)] = 1536  # pin survives
    svc = _rag_service(pins, live=[])  # sibling scan empty — pin is the backstop
    with (
        patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()),
        pytest.raises(EmbedDimensionConflict),
    ):
        await svc.create(
            project_id=pid,
            draft=_draft("gemini", "text-embedding-004"),  # 768-dim
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )


@pytest.mark.asyncio
async def test_failed_teardown_retains_pin_and_blocks_incompatible_create() -> None:
    # F-3, AC-1/AC-3: this is the regression. A Qdrant-failing teardown must leave
    # the pin intact — otherwise the 1536-dim collection survives while the project
    # accepts a 3072-dim config that can never index against it.
    pid = uuid.uuid4()
    pins = _FakePinRepo()
    pins.pins[(pid, PinKind.FILE_RAG.value)] = 1536
    svc = _rag_service(pins, live=[])
    store = AsyncMock()
    store.delete_collection.side_effect = RuntimeError("qdrant unreachable")

    p1, p2, p3 = _patch_qdrant(store)
    with p1, p2, p3:
        outcome = await svc.teardown_orphan_collection(project_id=pid)
    assert outcome is TeardownOutcome.FAILED
    assert outcome.pin_released is False  # AC-4: never audited as a drop
    assert pins.pins.get((pid, PinKind.FILE_RAG.value)) == 1536, "pin must survive a failed teardown"

    # The retained pin must still block the incompatible create. The create-path
    # retry (Q-5) fires, re-attempts the teardown, fails again, and falls through
    # to the typed conflict — the pre-F-3 rejection.
    with (
        p1,
        p2,
        p3,
        patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()),
        pytest.raises(EmbedDimensionConflict),
    ):
        await svc.create(
            project_id=pid,
            draft=_draft("openai", "text-embedding-3-large"),  # 3072-dim
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    assert pins.pins[(pid, PinKind.FILE_RAG.value)] == 1536  # still pinned, still closed


# ---------------------------------------------------------------------------
# Create-path teardown retry (§8.5, AC-7/AC-8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_retries_pending_teardown_then_repins() -> None:
    # AC-7: a previous delete left the pin at 1536 because Qdrant was down. Qdrant
    # is healthy now, so this 3072-dim create reclaims the orphan and re-pins.
    pid = uuid.uuid4()
    pins = _FakePinRepo()
    pins.pins[(pid, PinKind.FILE_RAG.value)] = 1536
    svc = _rag_service(pins, live=[])
    svc._configs.create.return_value = SimpleNamespace(
        id=uuid.uuid4(),
        name="cfg",
        chunk_strategy=SimpleNamespace(value="fixed"),
        embed_provider="openai",
        embed_model="text-embedding-3-large",
        rerank_enabled=False,
    )
    store = AsyncMock()
    store.delete_collection.return_value = True
    p1, p2, p3 = _patch_qdrant(store)
    with p1, p2, p3, patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()):
        await svc.create(
            project_id=pid,
            draft=_draft("openai", "text-embedding-3-large"),
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    store.delete_collection.assert_awaited_once_with(pid)
    assert pins.pins[(pid, PinKind.FILE_RAG.value)] == 3072


@pytest.mark.asyncio
async def test_create_at_pinned_dimension_never_calls_qdrant() -> None:
    # AC-8: the guard on the §2 non-goal. A create matching the pin needs no
    # teardown, so ordinary config CRUD must not depend on Qdrant being reachable.
    pid = uuid.uuid4()
    pins = _FakePinRepo()
    pins.pins[(pid, PinKind.FILE_RAG.value)] = 1536
    svc = _rag_service(pins, live=[])
    svc._configs.create.return_value = SimpleNamespace(
        id=uuid.uuid4(),
        name="cfg",
        chunk_strategy=SimpleNamespace(value="fixed"),
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        rerank_enabled=False,
    )
    store = AsyncMock()
    store.delete_collection.side_effect = AssertionError("teardown must not run for a matching pin")
    p1, p2, p3 = _patch_qdrant(store)
    with p1, p2, p3, patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()):
        await svc.create(
            project_id=pid,
            draft=_draft("openai", "text-embedding-3-small"),  # 1536-dim, matches
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    store.delete_collection.assert_not_awaited()
    assert pins.pins[(pid, PinKind.FILE_RAG.value)] == 1536


@pytest.mark.asyncio
async def test_create_retry_skips_teardown_when_another_holder_has_the_lock() -> None:
    # FU-3: losing the lock race means someone else is already retrying this exact
    # teardown. Waiting for the lock only to repeat their Qdrant call is what turned
    # N concurrent creates into N x teardown_timeout_s of held connections. Skip, and
    # let `ensure` block on the lock and read whatever they commit.
    pid = uuid.uuid4()
    pins = _FakePinRepo(lock_free=False)  # another transaction holds (pid, file_rag)
    pins.pins[(pid, PinKind.FILE_RAG.value)] = 1536
    svc = _rag_service(pins, live=[])
    store = AsyncMock()
    store.delete_collection.side_effect = AssertionError("a second retry must not run")
    p1, p2, p3 = _patch_qdrant(store)
    with (
        p1,
        p2,
        p3,
        patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()),
        pytest.raises(EmbedDimensionConflict),
    ):
        await svc.create(
            project_id=pid,
            draft=_draft("openai", "text-embedding-3-large"),  # 3072-dim
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    store.delete_collection.assert_not_awaited()
    assert pins.pins[(pid, PinKind.FILE_RAG.value)] == 1536  # still fails closed


@pytest.mark.asyncio
async def test_create_retry_skips_teardown_when_live_config_races_in() -> None:
    # AC-5: a live sibling means the collection is in use. The create-path retry must
    # not drop it; the sibling scan rejects the mismatch instead.
    pid = uuid.uuid4()
    pins = _FakePinRepo()
    pins.pins[(pid, PinKind.FILE_RAG.value)] = 1536
    sibling = SimpleNamespace(embed_provider="openai", embed_model="text-embedding-3-small")
    svc = _rag_service(pins, live=[sibling])
    store = AsyncMock()
    p1, p2, p3 = _patch_qdrant(store)
    with (
        p1,
        p2,
        p3,
        patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()),
        pytest.raises(EmbedDimensionConflict),
    ):
        await svc.create(
            project_id=pid,
            draft=_draft("openai", "text-embedding-3-large"),  # 3072-dim
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    store.delete_collection.assert_not_awaited()
    assert pins.pins[(pid, PinKind.FILE_RAG.value)] == 1536


# ---------------------------------------------------------------------------
# Teardown failure is fail-closed in all three products (§8.1, AC-1/AC-3/AC-4)
# ---------------------------------------------------------------------------


def _knowmap_service(pins: _FakePinRepo, live: list[Any]) -> Any:
    from contexts.knowledge.application.knowmap_config_service import KnowmapConfigService

    svc = KnowmapConfigService(db=AsyncMock())
    svc._configs = AsyncMock()
    svc._configs.list_for_project.return_value = live
    svc._pins = pins
    return svc


def _graphrag_service(pins: _FakePinRepo, live: list[Any]) -> Any:
    from contexts.knowledge.application.graphrag_config_service import GraphRagConfigService

    svc = GraphRagConfigService(db=AsyncMock())
    svc._configs = AsyncMock()
    svc._configs.list_for_project.return_value = live
    svc._pins = pins
    return svc


_GRAPH_STORE = "contexts.knowledge.infrastructure.graphrag_vector_store.GraphRagVectorStore"
_RAG_STORE = "contexts.knowledge.infrastructure.qdrant_store.QdrantStore"

_PRODUCTS = [
    pytest.param(_rag_service, PinKind.FILE_RAG, _RAG_STORE, id="file_rag"),
    pytest.param(_knowmap_service, PinKind.KNOWMAP, _GRAPH_STORE, id="knowmap"),
    pytest.param(_graphrag_service, PinKind.GRAPHRAG, _GRAPH_STORE, id="graphrag"),
]


def _patch_teardown(store_target: str, store: Any) -> Any:
    settings = SimpleNamespace(qdrant=SimpleNamespace(url="http://q", api_key="", teardown_timeout_s=10.0))
    return (
        patch(
            "contexts.knowledge.infrastructure.qdrant_teardown.AsyncQdrantClient", return_value=AsyncMock()
        ),
        patch("app.config.settings.get_settings", return_value=settings),
        patch(store_target, return_value=store),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("factory", "kind", "store_target"), _PRODUCTS)
async def test_qdrant_failure_retains_pin_in_every_product(
    factory: Any, kind: PinKind, store_target: str
) -> None:
    # AC-1/AC-3: all three products copied the clear-before-drop lifecycle, so all
    # three must now fail closed — the pin survives a Qdrant error.
    pid = uuid.uuid4()
    pins = _FakePinRepo()
    pins.pins[(pid, kind.value)] = 1536
    svc = factory(pins, [])
    store = AsyncMock()
    store.delete_collection.side_effect = RuntimeError("qdrant unreachable")
    p1, p2, p3 = _patch_teardown(store_target, store)
    with p1, p2, p3:
        outcome = await svc.teardown_orphan_collection(project_id=pid)
    assert outcome is TeardownOutcome.FAILED
    assert pins.pins[(pid, kind.value)] == 1536


@pytest.mark.asyncio
@pytest.mark.parametrize(("factory", "kind", "store_target"), _PRODUCTS)
@pytest.mark.parametrize(
    ("dropped", "expected"), [(True, TeardownOutcome.DROPPED), (False, TeardownOutcome.ABSENT)]
)
async def test_confirmed_absence_releases_pin_in_every_product(
    factory: Any, kind: PinKind, store_target: str, dropped: bool, expected: TeardownOutcome
) -> None:
    # AC-4: dropped and already-absent both confirm the collection is gone.
    pid = uuid.uuid4()
    pins = _FakePinRepo()
    pins.pins[(pid, kind.value)] = 1536
    svc = factory(pins, [])
    store = AsyncMock()
    store.delete_collection.return_value = dropped
    p1, p2, p3 = _patch_teardown(store_target, store)
    with p1, p2, p3:
        outcome = await svc.teardown_orphan_collection(project_id=pid)
    assert outcome is expected
    assert outcome.pin_released is True
    assert (pid, kind.value) not in pins.pins


@pytest.mark.asyncio
async def test_teardown_timeout_retains_pin() -> None:
    # AC-9: a hung Qdrant must not hold the advisory lock open. The bound fires,
    # the pin is retained, and the retry paths reclaim the collection later.
    import asyncio

    pid = uuid.uuid4()
    pins = _FakePinRepo()
    pins.pins[(pid, PinKind.FILE_RAG.value)] = 1536
    svc = _rag_service(pins, live=[])

    async def _hang(_pid: uuid.UUID) -> bool:
        await asyncio.sleep(10)
        return True

    store = AsyncMock()
    store.delete_collection.side_effect = _hang
    client = AsyncMock()
    settings = SimpleNamespace(qdrant=SimpleNamespace(url="http://q", api_key="", teardown_timeout_s=0.01))
    with (
        patch("contexts.knowledge.infrastructure.qdrant_teardown.AsyncQdrantClient", return_value=client),
        patch("app.config.settings.get_settings", return_value=settings),
        patch(_RAG_STORE, return_value=store),
    ):
        outcome = await svc.teardown_orphan_collection(project_id=pid)
    assert outcome is TeardownOutcome.FAILED
    assert pins.pins[(pid, PinKind.FILE_RAG.value)] == 1536
    client.close.assert_awaited_once()  # the timeout path still closes the client


# ---------------------------------------------------------------------------
# File RAG runtime dimension guard (§8.3, AC-5)
# ---------------------------------------------------------------------------


class _FakeQClient:
    def __init__(self, *, exists: bool, size: int | None = None) -> None:
        self._exists = exists
        self._size = size
        self.created = False

    async def collection_exists(self, name: str) -> bool:
        return self._exists

    async def get_collection(self, name: str) -> Any:
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=self._size)))
        )

    async def create_collection(self, **kwargs: Any) -> None:
        self.created = True


@pytest.mark.asyncio
async def test_ensure_collection_raises_on_dimension_mismatch() -> None:
    client = _FakeQClient(exists=True, size=1536)
    store = QdrantStore(client)  # type: ignore[arg-type]
    with pytest.raises(RagCollectionDimensionMismatch):
        await store.ensure_collection(uuid.uuid4(), vector_size=3072)


@pytest.mark.asyncio
async def test_ensure_collection_matching_dimension_is_noop() -> None:
    client = _FakeQClient(exists=True, size=1536)
    store = QdrantStore(client)  # type: ignore[arg-type]
    await store.ensure_collection(uuid.uuid4(), vector_size=1536)  # no raise
    assert client.created is False


@pytest.mark.asyncio
async def test_ensure_collection_creates_when_absent() -> None:
    client = _FakeQClient(exists=False)
    store = QdrantStore(client)  # type: ignore[arg-type]
    await store.ensure_collection(uuid.uuid4(), vector_size=1024)
    assert client.created is True
