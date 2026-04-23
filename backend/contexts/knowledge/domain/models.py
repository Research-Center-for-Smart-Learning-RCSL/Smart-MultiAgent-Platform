"""Knowledge (RAG) domain dataclasses — framework-free."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class ChunkStrategy(str, enum.Enum):
    FIXED = "fixed"
    SEMANTIC = "semantic"


class DocumentStatus(str, enum.Enum):
    INGESTING = "ingesting"
    READY = "ready"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class ScanStatus(str, enum.Enum):
    PENDING = "pending"
    CLEAN = "clean"
    QUARANTINED = "quarantined"
    SKIPPED = "skipped"


# R10.05 — embedding model whitelist.
EMBED_MODEL_WHITELIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("openai", "text-embedding-3-small"),
        ("openai", "text-embedding-3-large"),
        ("gemini", "text-embedding-004"),
        ("voyage", "voyage-3"),
    }
)


# R10.04 — chunk parameter defaults.
DEFAULT_FIXED_CHUNK_PARAMS: dict[str, int] = {
    "chunk_size_tokens": 512,
    "chunk_overlap_tokens": 64,
}
DEFAULT_SEMANTIC_CHUNK_PARAMS: dict[str, float | int] = {
    "max_tokens_per_chunk": 512,
    "similarity_threshold": 0.6,
}


@dataclass(frozen=True, slots=True)
class RagConfig:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    chunk_strategy: ChunkStrategy
    chunk_params: dict[str, Any]
    embed_key_id: uuid.UUID | None
    embed_provider: str
    embed_model: str
    rerank_enabled: bool
    rerank_key_id: uuid.UUID | None
    rerank_provider: str | None
    rerank_model: str | None
    top_k: int
    created_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class RagDocument:
    id: uuid.UUID
    rag_config_id: uuid.UUID
    filename: str
    mime: str
    size_bytes: int
    sha256: str
    minio_path: str
    status: DocumentStatus
    scan_status: ScanStatus
    scan_at: datetime | None
    uploaded_by: uuid.UUID | None
    uploaded_at: datetime


@dataclass(frozen=True, slots=True)
class RagChunk:
    id: int
    document_id: uuid.UUID
    chunk_idx: int
    text: str
    qdrant_point_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class RagConfigDraft:
    name: str
    chunk_strategy: ChunkStrategy
    chunk_params: dict[str, Any] = field(default_factory=dict)
    embed_key_id: uuid.UUID | None = None
    embed_provider: str = ""
    embed_model: str = ""
    rerank_enabled: bool = False
    rerank_key_id: uuid.UUID | None = None
    rerank_provider: str | None = None
    rerank_model: str | None = None
    top_k: int = 8


__all__ = [
    "ChunkStrategy",
    "DEFAULT_FIXED_CHUNK_PARAMS",
    "DEFAULT_SEMANTIC_CHUNK_PARAMS",
    "DocumentStatus",
    "EMBED_MODEL_WHITELIST",
    "RagChunk",
    "RagConfig",
    "RagConfigDraft",
    "RagDocument",
    "ScanStatus",
]
