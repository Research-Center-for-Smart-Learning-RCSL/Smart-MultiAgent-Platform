"""Knowledge Map (knowmap) domain dataclasses — framework-free.

The Axis-1 GraphRAG built from uploaded documents (R11.12). Reuses the file-RAG
``ChunkStrategy`` / ``DocumentStatus`` / ``ScanStatus`` enums and the shared
``BuildState`` from the graph domain rather than minting parallel copies; only the
config/document/chunk shapes are new.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from contexts.knowledge.domain.graphrag import BuildState
from contexts.knowledge.domain.models import ChunkStrategy, DocumentStatus, ScanStatus


@dataclass(frozen=True, slots=True)
class KnowmapConfig:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    builder_key_group_id: uuid.UUID
    chunk_strategy: ChunkStrategy
    chunk_params: dict[str, Any]
    embed_provider: str | None
    embed_model: str | None
    embed_dim: int | None
    last_build_at: datetime | None
    last_build_state: BuildState
    last_build_error: str | None
    created_at: datetime
    deleted_at: datetime | None
    # Always None — a Knowledge Map is non-temporal (R11.21). Present only to
    # satisfy the shared engine's ``ConfigLike`` port (the temporal fields stay
    # null/unused here, as the spec's non-goals state).
    recency_half_life_days: float | None = None
    # F-12: monotonic corpus revision, bumped in the same transaction as every
    # committed document mutation (add / reprocess / delete). The build dedup job
    # id is keyed on it so distinct corpus states never collide, and
    # ``built_corpus_revision`` records the revision the last successful build
    # processed for the completion re-check.
    corpus_revision: int = 0
    built_corpus_revision: int | None = None
    # F-4: when the current build entered RUNNING. Unlike ``last_build_at``, which
    # only moves on a terminal outcome, this makes a stuck build's age readable.
    # ``None`` for a config that has not built since migration 0059.
    build_started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class KnowmapDocument:
    id: uuid.UUID
    knowmap_config_id: uuid.UUID
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
    # Strict per-agent allowlist. Empty = no agent may see this document's evidence.
    agent_ids: tuple[uuid.UUID, ...] = ()
    ingest_attempt: int = 0
    ingest_claim_token: uuid.UUID | None = None
    ingest_claim_until: datetime | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class KnowmapChunk:
    id: int
    document_id: uuid.UUID
    chunk_idx: int
    text: str


@dataclass(frozen=True, slots=True)
class KnowmapConfigDraft:
    name: str
    builder_key_group_id: uuid.UUID
    chunk_strategy: ChunkStrategy = ChunkStrategy.FIXED
    chunk_params: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "KnowmapChunk",
    "KnowmapConfig",
    "KnowmapConfigDraft",
    "KnowmapDocument",
]
