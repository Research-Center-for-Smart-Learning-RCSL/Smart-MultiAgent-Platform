"""Composition root for knowledge ingestion services."""

from __future__ import annotations

from typing import Any

from minio import Minio
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.knowledge.application.ingest_ports import RagVectorIngestPort
from contexts.knowledge.application.ingest_service import IngestService
from contexts.knowledge.application.knowmap_ingest_service import KnowmapIngestService
from contexts.knowledge.application.knowmap_tus_finalizer import KnowmapTusFinalizer
from contexts.knowledge.application.ports import BlobStore, Embedder
from contexts.knowledge.application.rag_tus_finalizer import RagTusFinalizer
from contexts.knowledge.infrastructure.blob_store import MinioBlobStore
from contexts.knowledge.infrastructure.chunkers import chunk_document
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapChunkRepository,
    KnowmapConfigRepository,
    KnowmapDocumentRepository,
)
from contexts.knowledge.infrastructure.qdrant_store import QdrantStore
from contexts.knowledge.infrastructure.repositories import (
    RagChunkRepository,
    RagConfigRepository,
    RagDocumentRepository,
)
from shared_kernel.storage import get_minio_client


class KnowledgeIngestWiring:
    def __init__(self, db: AsyncSession, *, scan_required: bool | None = None) -> None:
        self._db = db
        self._scan_required = (
            get_settings().security.file_scan_enabled if scan_required is None else scan_required
        )

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
            scan_required=self._scan_required,
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
            scan_required=self._scan_required,
            bucket=bucket,
        )

    def rag_upload_service(
        self,
        *,
        embedder: Any,
    ) -> tuple[IngestService, AsyncQdrantClient]:
        settings = get_settings()
        blob = MinioBlobStore(
            Minio(
                settings.minio.endpoint,
                access_key=settings.minio.root_access_key,
                secret_key=settings.minio.root_secret_key,
                secure=settings.minio.use_tls,
                region=settings.minio.region,
            )
        )
        qclient = AsyncQdrantClient(
            url=settings.qdrant.url,
            api_key=settings.qdrant.api_key or None,
        )
        return (
            self.rag_service(
                blob=blob,
                embedder=embedder,
                qdrant=QdrantStore(qclient),
                bucket=settings.minio.bucket_rag_sources,
            ),
            qclient,
        )

    def knowmap_upload_service(self, *, embedder: Any) -> KnowmapIngestService:
        settings = get_settings()
        blob = MinioBlobStore(
            Minio(
                settings.minio.endpoint,
                access_key=settings.minio.root_access_key,
                secret_key=settings.minio.root_secret_key,
                secure=settings.minio.use_tls,
                region=settings.minio.region,
            )
        )
        return self.knowmap_service(
            blob=blob,
            embedder=embedder,
            bucket=settings.minio.bucket_knowmap_sources,
        )

    def rag_finalizer(self) -> RagTusFinalizer:
        return RagTusFinalizer(
            self._db,
            configs=RagConfigRepository(self._db),
            documents=RagDocumentRepository(self._db),
            staged_source=get_minio_client(),
            scan_required=self._scan_required,
        )

    def knowmap_finalizer(self) -> KnowmapTusFinalizer:
        return KnowmapTusFinalizer(
            self._db,
            configs=KnowmapConfigRepository(self._db),
            documents=KnowmapDocumentRepository(self._db),
            staged_source=get_minio_client(),
            scan_required=self._scan_required,
        )


__all__ = ["KnowledgeIngestWiring"]
