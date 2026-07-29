"""Composition root for knowledge ingestion services."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.ingest_ports import RagVectorIngestPort
from contexts.knowledge.application.ingest_service import IngestService
from contexts.knowledge.application.knowmap_ingest_service import KnowmapIngestService
from contexts.knowledge.application.knowmap_tus_finalizer import KnowmapTusFinalizer
from contexts.knowledge.application.ports import BlobStore, Embedder
from contexts.knowledge.application.rag_tus_finalizer import RagTusFinalizer
from contexts.knowledge.infrastructure.chunkers import chunk_document
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapChunkRepository,
    KnowmapConfigRepository,
    KnowmapDocumentRepository,
)
from contexts.knowledge.infrastructure.repositories import (
    RagChunkRepository,
    RagConfigRepository,
    RagDocumentRepository,
)
from shared_kernel.storage import get_minio_client


class KnowledgeIngestWiring:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    def rag_service(
        self,
        *,
        blob: BlobStore,
        embedder: Embedder,
        qdrant: RagVectorIngestPort,
        bucket: str = "rag-sources",
    ) -> IngestService:
        return IngestService(
            self._db,
            blob=blob,
            embedder=embedder,
            qdrant=qdrant,
            configs=RagConfigRepository(self._db),
            documents=RagDocumentRepository(self._db),
            chunks=RagChunkRepository(self._db),
            chunker=chunk_document,
            bucket=bucket,
        )

    def knowmap_service(
        self,
        *,
        blob: BlobStore,
        embedder: Embedder,
        bucket: str = "knowmap-sources",
    ) -> KnowmapIngestService:
        return KnowmapIngestService(
            self._db,
            blob=blob,
            embedder=embedder,
            configs=KnowmapConfigRepository(self._db),
            documents=KnowmapDocumentRepository(self._db),
            chunks=KnowmapChunkRepository(self._db),
            chunker=chunk_document,
            bucket=bucket,
        )

    def rag_finalizer(self) -> RagTusFinalizer:
        return RagTusFinalizer(
            self._db,
            configs=RagConfigRepository(self._db),
            documents=RagDocumentRepository(self._db),
            staged_source=get_minio_client(),
        )

    def knowmap_finalizer(self) -> KnowmapTusFinalizer:
        return KnowmapTusFinalizer(
            self._db,
            configs=KnowmapConfigRepository(self._db),
            documents=KnowmapDocumentRepository(self._db),
            staged_source=get_minio_client(),
        )


__all__ = ["KnowledgeIngestWiring"]
