"""Protocols for GraphRAG I/O surfaces (E.7 / R11.03–R11.06).

Kept as Protocols so the 2PC state machine in :mod:`graphrag_builder` can
be driven by trivial fakes in unit tests — real clients (Neo4j, Redis,
Qdrant, LLM providers) live in :mod:`contexts.knowledge.infrastructure`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from contexts.knowledge.domain.graphrag import BuildState, Triple

__all__ = [
    "BuildLockStore",
    "ConfigLike",
    "DeltaMessage",
    "GraphRagConfigRepositoryPort",
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

    async def refresh(self, config_id: uuid.UUID, *, ttl_s: int) -> bool:
        """Extend the lock's TTL iff this holder still owns it.

        Returns False if the lock was lost (expired and possibly re-acquired by
        another build). Callers use this at phase boundaries to keep a live
        build's lock alive and to fail closed rather than write concurrently.
        """


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

    async def set_current(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
        ttl_s: int,
    ) -> None:
        """Record the in-flight build id authoritatively (audit C4)."""

    async def get_current(
        self,
        *,
        config_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Return the authoritatively-recorded in-flight build id, if any."""

    async def clear_current(
        self,
        *,
        config_id: uuid.UUID,
    ) -> None:
        """Drop the in-flight build-id pointer on a terminal transition."""


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
        project_id: uuid.UUID,
        build_id: uuid.UUID,
        triples: list[Triple],
    ) -> int:
        """Upsert ``triples`` tagged with ``build_id``; returns count.

        ``project_id`` is stamped on every ``:Entity`` node so an orphaned
        subgraph (no surviving Postgres row) stays self-describing for the
        reconciler's Qdrant sweep (R11.04 backstop).
        """

    async def delete_by_build(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
    ) -> None:
        """Drop all entities/edges tagged with ``build_id``."""

    async def delete_all(self, *, config_id: uuid.UUID) -> None:
        """Drop the entire subgraph for a config (delete cascade, §22.8)."""

    async def list_config_ids(
        self,
    ) -> list[tuple[uuid.UUID, uuid.UUID | None]]:
        """Return ``(graphrag_config_id, project_id)`` for every live subgraph.

        ``project_id`` is ``None`` for legacy nodes written before the
        self-describing property existed. Used by the reconciler orphan sweep
        to find subgraphs whose Postgres config row is gone (R11.04 backstop).
        """

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


class ConfigLike(Protocol):
    """Structural view of the graph-config fields the engine actually reads.

    :class:`~contexts.knowledge.domain.graphrag.GraphRagConfig` (and any future
    Knowledge Map config) satisfies this without inheritance, so the builder /
    retrieve / reconciler need not depend on the concrete domain type. Members
    are read-only ``property`` so that a frozen dataclass satisfies the Protocol.
    """

    @property
    def id(self) -> uuid.UUID: ...

    @property
    def project_id(self) -> uuid.UUID: ...

    @property
    def builder_key_group_id(self) -> uuid.UUID: ...

    @property
    def last_build_state(self) -> BuildState: ...

    @property
    def last_build_at(self) -> datetime | None: ...


class GraphRagConfigRepositoryPort(Protocol):
    """Repository surface the engine depends on (implemented in infrastructure).

    Declared in the application layer so the engine imports no concrete
    repository; the infrastructure ``GraphRagConfigRepository`` is injected at
    the wiring edge.
    """

    async def get(
        self,
        config_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> ConfigLike | None: ...

    async def set_state(
        self,
        *,
        config_id: uuid.UUID,
        state: BuildState,
        error: str | None = None,
        stamp_built_at: bool = False,
    ) -> None: ...

    async def list_in_state(self, state: BuildState) -> Sequence[ConfigLike]: ...

    async def list_all_ids(self, *, include_deleted: bool = False) -> set[uuid.UUID]: ...
