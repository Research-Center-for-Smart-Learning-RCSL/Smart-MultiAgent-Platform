"""F-20 (R10.04) — chunk parameters are immutable once a config has documents.

Chunking is a live read of the config at each ingest with no per-document
provenance, so a chunk-param change after documents exist would leave the corpus
split between two chunking policies. The config-update services reject a
*changing* ``chunk_params`` patch (409 ``ChunkParamsImmutable``) once a locking
document exists, while allowing it on an empty config and always allowing an
identical no-op (the full-form detail-view save resends ``chunk_params`` every
time — Q-2).

Two layers are pinned, matching the repo's conventions:

* statement level — ``count_locking_for_config`` counts only ``INGESTING``/
  ``READY`` documents, never ``FAILED``/``QUARANTINED`` (Q-3), and keys on
  ``DocumentStatus`` rather than ``scan_status``;
* application orchestration — ``RagConfigService.update`` /
  ``KnowmapConfigService.update`` reject a changing patch iff a locking document
  exists, allow it otherwise, and never block a no-op patch.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest

from contexts.knowledge.domain.errors import ChunkParamsImmutable
from contexts.knowledge.domain.models import ChunkStrategy
from contexts.knowledge.infrastructure.knowmap_repositories import KnowmapDocumentRepository
from contexts.knowledge.infrastructure.repositories import RagDocumentRepository

# --------------------------------------------------------------------------
# Statement-level: count_locking_for_config filters on DocumentStatus (Q-3)
# --------------------------------------------------------------------------


class _ScalarResult:
    def scalar_one(self) -> int:
        return 0


class _CapturingDb:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any, *_a: Any, **_k: Any) -> _ScalarResult:
        self.statements.append(stmt)
        return _ScalarResult()


def _sql(stmt: Any) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).replace(" ", "").lower()


@pytest.mark.asyncio
async def test_rag_count_locking_filters_ingesting_ready_only() -> None:
    db = _CapturingDb()
    repo = RagDocumentRepository(db)  # type: ignore[arg-type]
    config_id = uuid.uuid4()

    await repo.count_locking_for_config(config_id)

    assert len(db.statements) == 1
    sql = _sql(db.statements[0])
    assert "count(*)" in sql
    assert config_id.hex in sql
    # Q-3: only chunk-committing states lock; failed/quarantined never do.
    assert "'ingesting'" in sql
    assert "'ready'" in sql
    assert "'failed'" not in sql
    assert "'quarantined'" not in sql
    # The gate keys on DocumentStatus, not scan_status (F-5's clean-only selector).
    assert "scan_status" not in sql


@pytest.mark.asyncio
async def test_knowmap_count_locking_filters_ingesting_ready_only() -> None:
    db = _CapturingDb()
    repo = KnowmapDocumentRepository(db)  # type: ignore[arg-type]
    config_id = uuid.uuid4()

    await repo.count_locking_for_config(config_id)

    assert len(db.statements) == 1
    sql = _sql(db.statements[0])
    assert "count(*)" in sql
    assert config_id.hex in sql
    assert "'ingesting'" in sql
    assert "'ready'" in sql
    assert "'failed'" not in sql
    assert "'quarantined'" not in sql
    assert "scan_status" not in sql


# --------------------------------------------------------------------------
# Application orchestration: File RAG update guard
# --------------------------------------------------------------------------

_FIXED = {"chunk_size_tokens": 512, "chunk_overlap_tokens": 64}


def _rag_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        chunk_strategy=ChunkStrategy.FIXED,
        chunk_params=dict(_FIXED),
        rerank_enabled=False,
        rerank_key_id=None,
        rerank_provider=None,
    )


def _rag_service(cfg: SimpleNamespace, locking_docs: int) -> Any:
    from contexts.knowledge.application.config_service import RagConfigService

    svc = RagConfigService(AsyncMock())
    svc.get = AsyncMock(return_value=cfg)  # type: ignore[method-assign]
    svc._configs = AsyncMock()
    svc._configs.update.return_value = SimpleNamespace(id=cfg.id)
    svc._documents = AsyncMock()
    svc._documents.count_locking_for_config.return_value = locking_docs
    return svc


@pytest.mark.asyncio
async def test_rag_change_with_locking_document_rejected() -> None:
    cfg = _rag_cfg()
    svc = _rag_service(cfg, locking_docs=1)

    with pytest.raises(ChunkParamsImmutable):
        await svc.update(
            config_id=cfg.id,
            patch={"chunk_params": {"chunk_size_tokens": 256, "chunk_overlap_tokens": 64}},
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    # DB write never happened — the stored params are unchanged.
    svc._configs.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_rag_change_with_zero_locking_docs_allowed() -> None:
    cfg = _rag_cfg()
    # count == 0 stands in for both "no documents" and "only FAILED/QUARANTINED"
    # (the repo excludes those states — verified at statement level above).
    svc = _rag_service(cfg, locking_docs=0)

    with patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()):
        out = await svc.update(
            config_id=cfg.id,
            patch={"chunk_params": {"chunk_size_tokens": 256, "chunk_overlap_tokens": 64}},
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    assert out.id == cfg.id
    svc._configs.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_rag_identical_noop_patch_allowed_with_docs() -> None:
    cfg = _rag_cfg()
    svc = _rag_service(cfg, locking_docs=5)

    with patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()):
        out = await svc.update(
            config_id=cfg.id,
            # Same values, different key order — the full-form save path (Q-2).
            patch={"name": "renamed", "chunk_params": {"chunk_overlap_tokens": 64, "chunk_size_tokens": 512}},
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    assert out.id == cfg.id
    svc._configs.update.assert_awaited_once()


# --------------------------------------------------------------------------
# Application orchestration: Knowledge Map update guard (mirror)
# --------------------------------------------------------------------------


class _LockingDocs:
    """Patched-in ``KnowmapDocumentRepository`` returning a fixed locking count."""

    count: ClassVar[int] = 0

    def __init__(self, _db: Any) -> None:
        pass

    async def count_locking_for_config(self, _config_id: uuid.UUID) -> int:
        return _LockingDocs.count


def _knowmap_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        chunk_strategy=ChunkStrategy.FIXED,
        chunk_params=dict(_FIXED),
        builder_key_group_id=uuid.uuid4(),
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        last_build_at=None,
    )


def _knowmap_service(cfg: SimpleNamespace) -> Any:
    from contexts.knowledge.application.knowmap_config_service import KnowmapConfigService

    svc = KnowmapConfigService(AsyncMock())
    svc.get = AsyncMock(return_value=cfg)  # type: ignore[method-assign]
    svc._configs = AsyncMock()
    svc._configs.update.return_value = SimpleNamespace(id=cfg.id)
    return svc


def _patch_knowmap_docs(count: int) -> Any:
    _LockingDocs.count = count
    return patch(
        "contexts.knowledge.application.knowmap_config_service.KnowmapDocumentRepository",
        _LockingDocs,
    )


@pytest.mark.asyncio
async def test_knowmap_change_with_locking_document_rejected() -> None:
    cfg = _knowmap_cfg()
    svc = _knowmap_service(cfg)

    with _patch_knowmap_docs(1), pytest.raises(ChunkParamsImmutable):
        await svc.update(
            config_id=cfg.id,
            patch={"chunk_params": {"chunk_size_tokens": 256, "chunk_overlap_tokens": 64}},
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    svc._configs.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_knowmap_change_with_zero_locking_docs_allowed() -> None:
    cfg = _knowmap_cfg()
    svc = _knowmap_service(cfg)

    with (
        _patch_knowmap_docs(0),
        patch("contexts.knowledge.application.knowmap_config_service.audit.emit", new=AsyncMock()),
    ):
        out, _detached = await svc.update(
            config_id=cfg.id,
            patch={"chunk_params": {"chunk_size_tokens": 256, "chunk_overlap_tokens": 64}},
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    assert out.id == cfg.id
    svc._configs.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_knowmap_identical_noop_patch_allowed_with_docs() -> None:
    cfg = _knowmap_cfg()
    svc = _knowmap_service(cfg)

    with (
        _patch_knowmap_docs(3),
        patch("contexts.knowledge.application.knowmap_config_service.audit.emit", new=AsyncMock()),
    ):
        out, _detached = await svc.update(
            config_id=cfg.id,
            patch={"name": "renamed", "chunk_params": {"chunk_overlap_tokens": 64, "chunk_size_tokens": 512}},
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    assert out.id == cfg.id
    svc._configs.update.assert_awaited_once()
