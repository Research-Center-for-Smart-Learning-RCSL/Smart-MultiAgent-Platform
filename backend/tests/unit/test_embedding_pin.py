"""F-11 — durable, race-free project embedding-dimension pins.

Covers the pin repository's conflict decision per subsystem, the File RAG
delete-then-recreate lifecycle (drop-empty clears the pin + drops the collection,
recreate re-pins), a non-last delete retaining the pin, and the File RAG runtime
dimension guard. The concurrent first-create race is an integration test
(``tests/integration/test_embedding_pin_race.py``) since the advisory lock needs
a real Postgres.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from contexts.knowledge.application.config_service import RagConfigService
from contexts.knowledge.domain.embedding_pin import PinKind
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

    def __init__(self) -> None:
        self.pins: dict[tuple[uuid.UUID, str], int] = {}
        self.cleared: list[tuple[uuid.UUID, str]] = []

    async def acquire_lock(self, project_id: uuid.UUID, kind: PinKind) -> None:
        return None

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
    settings = SimpleNamespace(qdrant=SimpleNamespace(url="http://q", api_key=""))
    return (
        patch("qdrant_client.AsyncQdrantClient", return_value=AsyncMock()),
        patch("app.config.settings.get_settings", return_value=settings),
        patch("contexts.knowledge.infrastructure.qdrant_store.QdrantStore", return_value=store),
    )


@pytest.mark.asyncio
async def test_last_config_clears_pin_then_drops_collection() -> None:
    pid = uuid.uuid4()
    pins = _FakePinRepo()
    pins.pins[(pid, PinKind.FILE_RAG.value)] = 1536
    svc = _rag_service(pins, live=[])  # last config already soft-deleted
    # W1: the pin clear is atomic with the soft-delete (runs pre-commit).
    cleared = await svc.clear_pin_if_last_config(project_id=pid)
    assert cleared is True
    assert (pid, PinKind.FILE_RAG.value) not in pins.pins
    # W5: the collection drop runs post-commit, re-checked under the lock.
    store = AsyncMock()
    p1, p2, p3 = _patch_qdrant(store)
    with p1, p2, p3:
        dropped = await svc.drop_orphan_collection(project_id=pid)
    assert dropped is True
    store.delete_collection.assert_awaited_once_with(pid)


@pytest.mark.asyncio
async def test_clear_pin_keeps_pin_when_sibling_remains() -> None:
    pid = uuid.uuid4()
    pins = _FakePinRepo()
    pins.pins[(pid, PinKind.FILE_RAG.value)] = 1536
    sibling = SimpleNamespace(embed_provider="openai", embed_model="text-embedding-3-small")
    svc = _rag_service(pins, live=[sibling])
    cleared = await svc.clear_pin_if_last_config(project_id=pid)
    assert cleared is False
    assert pins.pins[(pid, PinKind.FILE_RAG.value)] == 1536


@pytest.mark.asyncio
async def test_drop_orphan_skips_when_config_reappeared() -> None:
    # W5: a concurrent create re-pinned the project between the pre-commit clear
    # and this post-commit drop — the re-check under the lock sees the live config
    # and leaves its collection intact rather than dropping it.
    pid = uuid.uuid4()
    pins = _FakePinRepo()
    reappeared = SimpleNamespace(embed_provider="openai", embed_model="text-embedding-3-small")
    svc = _rag_service(pins, live=[reappeared])
    store = AsyncMock()
    p1, p2, p3 = _patch_qdrant(store)
    with p1, p2, p3:
        dropped = await svc.drop_orphan_collection(project_id=pid)
    assert dropped is False
    store.delete_collection.assert_not_awaited()


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
