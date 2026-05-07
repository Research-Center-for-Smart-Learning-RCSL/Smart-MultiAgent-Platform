"""Protocols for GraphRAG I/O surfaces (E.7 / R11.03–R11.06).

Kept as Protocols so the 2PC state machine in :mod:`graphrag_builder` can
be driven by trivial fakes in unit tests — real clients (Neo4j, Redis,
Qdrant, LLM providers) live in :mod:`contexts.knowledge.infrastructure`.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from contexts.knowledge.domain.graphrag import Triple

__all__ = [
    "BuildLockStore",
    "DeltaMessage",
    "Neo4jDriver",
    "SnapshotStore",
    "TripleExtractor",
]


class BuildLockStore(Protocol):
    """Redis-backed per-config build lock (R11a.01, 10-min TTL)."""

    async def acquire(self, config_id: uuid.UUID, *, ttl_s: int) -> bool:
        """Return True iff the caller won the lock."""

    async def release(self, config_id: uuid.UUID) -> None:
        """Best-effort release; no-op if already expired."""


class SnapshotStore(Protocol):
    """Redis-backed pre-build Neo4j snapshot cache (R11.04 compensation)."""

    async def put(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
        snapshot: dict[str, Any],
        ttl_s: int,
    ) -> None: ...

    async def get(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
    ) -> dict[str, Any] | None: ...

    async def delete(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
    ) -> None: ...


class Neo4jDriver(Protocol):
    """Minimal surface for the GraphRAG subgraph operations."""

    async def snapshot_subgraph(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        """Read the current subgraph (tagged with the prior ``build_id``).

        Returns a dict that :meth:`restore_from_snapshot` can consume.
        """

    async def apply_triples(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
        triples: list[Triple],
    ) -> int:
        """Upsert ``triples`` tagged with ``build_id``; returns count."""

    async def delete_by_build(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
    ) -> None:
        """Drop all entities/edges tagged with ``build_id``."""

    async def delete_all(self, *, config_id: uuid.UUID) -> None:
        """Drop the entire subgraph for a config (delete cascade, §22.8)."""

    async def restore_from_snapshot(
        self,
        *,
        config_id: uuid.UUID,
        snapshot: dict[str, Any],
    ) -> None:
        """Re-hydrate a subgraph from a prior :meth:`snapshot_subgraph`."""

    async def traverse(
        self,
        *,
        config_id: uuid.UUID,
        seed_entities: list[str],
        hops: int,
    ) -> list[dict[str, Any]]:
        """Return 1–2 hop edges from ``seed_entities`` as raw dict rows."""


class DeltaMessage(Protocol):
    """Shape the extractor expects for a single chat message in the delta."""

    id: uuid.UUID
    role: str
    content: str


class TripleExtractor(Protocol):
    """LLM-backed (subject, relation, object, confidence, evidence) extractor."""

    async def extract(
        self,
        *,
        config_id: uuid.UUID,
        builder_key_group_id: uuid.UUID,
        messages: list[DeltaMessage],
    ) -> list[Triple]: ...
