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


# R10.05 — embedding model whitelist + each model's vector dimension. A project
# shares one Qdrant collection (`rag_{project_id}`) sized to the first config's
# dimension, so all of a project's configs must embed at the same dimension —
# enforced at config create/update against this map (single source of truth; the
# infrastructure embedder reads it too).
EMBED_MODEL_DIMENSIONS: dict[tuple[str, str], int] = {
    ("openai", "text-embedding-3-small"): 1536,
    ("openai", "text-embedding-3-large"): 3072,
    ("gemini", "text-embedding-004"): 768,
    ("voyage", "voyage-3"): 1024,
}

EMBED_MODEL_WHITELIST: frozenset[tuple[str, str]] = frozenset(EMBED_MODEL_DIMENSIONS)


def embed_dimension(provider: str, model: str) -> int:
    """Vector dimension for a whitelisted embedding model."""
    try:
        return EMBED_MODEL_DIMENSIONS[(provider, model)]
    except KeyError as exc:
        raise ValueError(f"unknown embedding model ({provider}, {model})") from exc


# Recommended default model per embedding provider — surfaced to the RAG-config
# UI as the pre-selected choice. Each entry MUST be a key of
# EMBED_MODEL_DIMENSIONS (the whitelist is the single source of truth).
DEFAULT_EMBED_MODELS: dict[str, str] = {
    "openai": "text-embedding-3-small",
    "gemini": "text-embedding-004",
    "voyage": "voyage-3",
}

_embed_providers = {p for p, _ in EMBED_MODEL_DIMENSIONS}
assert set(DEFAULT_EMBED_MODELS) == _embed_providers, (
    "DEFAULT_EMBED_MODELS and EMBED_MODEL_DIMENSIONS must cover the same providers"
)
assert all(
    (p, m) in EMBED_MODEL_DIMENSIONS for p, m in DEFAULT_EMBED_MODELS.items()
), "every DEFAULT_EMBED_MODELS value must be a key of EMBED_MODEL_DIMENSIONS"


@dataclass(frozen=True, slots=True)
class EmbedModelOption:
    model: str
    dimension: int


@dataclass(frozen=True, slots=True)
class EmbedCatalogEntry:
    """One provider's whitelisted embedding models plus its recommended default."""

    provider: str
    models: tuple[EmbedModelOption, ...]
    default: str


def embedding_catalog() -> tuple[EmbedCatalogEntry, ...]:
    """Group the embedding whitelist by provider for the RAG-config UI."""
    by_provider: dict[str, list[EmbedModelOption]] = {}
    for (provider, model), dim in EMBED_MODEL_DIMENSIONS.items():
        by_provider.setdefault(provider, []).append(EmbedModelOption(model=model, dimension=dim))
    return tuple(
        EmbedCatalogEntry(provider=p, models=tuple(models), default=DEFAULT_EMBED_MODELS[p])
        for p, models in by_provider.items()
    )


# R10.04 — chunk parameter defaults.
DEFAULT_FIXED_CHUNK_PARAMS: dict[str, int] = {
    "chunk_size_tokens": 512,
    "chunk_overlap_tokens": 64,
}
# similarity_threshold is compared against the cosine of a sentence to the
# running chunk centroid; for in-topic prose that cosine often sits well below
# 0.6, so a high default over-fragments into ~1-sentence chunks. Default low —
# split only on a clear topic shift; max_tokens_per_chunk is the reliable bound.
DEFAULT_SEMANTIC_CHUNK_PARAMS: dict[str, float | int] = {
    "max_tokens_per_chunk": 512,
    "similarity_threshold": 0.3,
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
    # Strict per-agent allowlist. Empty = no agent may retrieve this document.
    agent_ids: tuple[uuid.UUID, ...] = ()


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
    "DEFAULT_EMBED_MODELS",
    "DEFAULT_FIXED_CHUNK_PARAMS",
    "DEFAULT_SEMANTIC_CHUNK_PARAMS",
    "DocumentStatus",
    "EMBED_MODEL_DIMENSIONS",
    "EMBED_MODEL_WHITELIST",
    "EmbedCatalogEntry",
    "EmbedModelOption",
    "RagChunk",
    "RagConfig",
    "RagConfigDraft",
    "RagDocument",
    "ScanStatus",
    "embed_dimension",
    "embedding_catalog",
]
