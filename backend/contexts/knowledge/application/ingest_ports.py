"""Narrow persistence and storage ports consumed by knowledge ingestion."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from contexts.knowledge.application.ports import Embedder
from contexts.knowledge.domain.knowmap import KnowmapConfig, KnowmapDocument
from contexts.knowledge.domain.models import (
    ChunkStrategy,
    DocumentStatus,
    IngestClaim,
    RagConfig,
    RagDocument,
)


class RagConfigIngestPort(Protocol):
    async def get(
        self,
        config_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> RagConfig | None: ...


class RagDocumentIngestPort(Protocol):
    async def find_by_sha(
        self,
        *,
        rag_config_id: uuid.UUID,
        sha256: str,
    ) -> RagDocument | None: ...

    async def create(
        self,
        *,
        rag_config_id: uuid.UUID,
        filename: str,
        mime: str,
        size_bytes: int,
        sha256: str,
        minio_path: str,
        uploaded_by: uuid.UUID | None,
        agent_ids: Sequence[uuid.UUID] = (),
    ) -> RagDocument: ...

    async def set_agents(
        self,
        *,
        document_id: uuid.UUID,
        agent_ids: Sequence[uuid.UUID],
    ) -> RagDocument | None: ...

    async def set_status(
        self,
        *,
        document_id: uuid.UUID,
        status: DocumentStatus,
    ) -> None: ...

    async def claim_for_reingest(self, document_id: uuid.UUID) -> IngestClaim | None: ...

    async def claim_initial(self, document_id: uuid.UUID) -> IngestClaim | None: ...

    async def owns_claim(self, document_id: uuid.UUID, claim: IngestClaim) -> bool: ...

    async def lock_for_ingest(self, document_id: uuid.UUID) -> None: ...

    async def finish_claim(
        self,
        *,
        document_id: uuid.UUID,
        claim: IngestClaim,
        status: DocumentStatus,
    ) -> bool: ...

    async def get(self, document_id: uuid.UUID) -> RagDocument | None: ...

    async def require(self, document_id: uuid.UUID) -> RagDocument: ...


class RagChunkIngestPort(Protocol):
    async def insert_many(self, chunks: Sequence[dict[str, Any]]) -> None: ...

    async def delete_for_document(self, document_id: uuid.UUID) -> None: ...


class RagVectorIngestPort(Protocol):
    async def ensure_collection(
        self,
        project_id: uuid.UUID,
        *,
        vector_size: int,
    ) -> None: ...

    async def upsert_chunks(
        self,
        *,
        project_id: uuid.UUID,
        points: Iterable[tuple[uuid.UUID, list[float], dict[str, Any]]],
    ) -> None: ...

    async def delete_document(
        self,
        *,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None: ...


class KnowmapConfigIngestPort(Protocol):
    async def get(
        self,
        config_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> KnowmapConfig | None: ...

    async def bump_corpus_revision(self, config_id: uuid.UUID) -> int: ...


class KnowmapDocumentIngestPort(Protocol):
    async def find_by_sha(
        self,
        *,
        knowmap_config_id: uuid.UUID,
        sha256: str,
    ) -> KnowmapDocument | None: ...

    async def create(
        self,
        *,
        knowmap_config_id: uuid.UUID,
        filename: str,
        mime: str,
        size_bytes: int,
        sha256: str,
        minio_path: str,
        uploaded_by: uuid.UUID | None,
        agent_ids: Sequence[uuid.UUID] = (),
    ) -> KnowmapDocument: ...

    async def set_agents(
        self,
        *,
        document_id: uuid.UUID,
        agent_ids: Sequence[uuid.UUID],
    ) -> KnowmapDocument | None: ...

    async def set_status(
        self,
        *,
        document_id: uuid.UUID,
        status: DocumentStatus,
    ) -> None: ...

    async def claim_for_reingest(self, document_id: uuid.UUID) -> IngestClaim | None: ...

    async def claim_initial(self, document_id: uuid.UUID) -> IngestClaim | None: ...

    async def owns_claim(self, document_id: uuid.UUID, claim: IngestClaim) -> bool: ...

    async def lock_for_ingest(self, document_id: uuid.UUID) -> None: ...

    async def finish_claim(
        self,
        *,
        document_id: uuid.UUID,
        claim: IngestClaim,
        status: DocumentStatus,
    ) -> bool: ...

    async def get(self, document_id: uuid.UUID) -> KnowmapDocument | None: ...

    async def require(self, document_id: uuid.UUID) -> KnowmapDocument: ...


class KnowmapChunkIngestPort(Protocol):
    async def insert_many(self, chunks: Sequence[dict[str, Any]]) -> None: ...

    async def delete_for_document(self, document_id: uuid.UUID) -> None: ...


class StagedSourcePort(Protocol):
    @property
    def rag_sources_bucket(self) -> str: ...

    @property
    def knowmap_sources_bucket(self) -> str: ...

    async def put_file(
        self,
        *,
        bucket: str,
        key: str,
        file_path: str,
        content_type: str = "application/octet-stream",
    ) -> None: ...


class DocumentChunker(Protocol):
    async def __call__(
        self,
        text: str,
        *,
        strategy: ChunkStrategy,
        params: dict[str, Any],
        embedder: Embedder,
    ) -> list[str]: ...


__all__ = [
    "DocumentChunker",
    "KnowmapChunkIngestPort",
    "KnowmapConfigIngestPort",
    "KnowmapDocumentIngestPort",
    "RagChunkIngestPort",
    "RagConfigIngestPort",
    "RagDocumentIngestPort",
    "RagVectorIngestPort",
    "StagedSourcePort",
]
