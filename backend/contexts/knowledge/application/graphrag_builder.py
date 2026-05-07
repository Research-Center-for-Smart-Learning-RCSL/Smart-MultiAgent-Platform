"""GraphRAG build orchestrator — 2PC state machine (E.7 / §11.2a / R11.04).

State transitions:

    idle → running → neo4j_committed → qdrant_committed → idle

Phase-1 failure (triple extraction or Neo4j write) → state ``failed``;
nothing was committed anywhere so there is nothing to compensate.

Phase-2 failure (Qdrant upsert after Neo4j commit) → state
``failed_compensating``. The reconciliation loop
(:mod:`graphrag_reconciler`) retries the Qdrant phase up to 5× with
exponential backoff; if that is exhausted it rolls Neo4j back from the
Redis snapshot and finalises the state as ``failed``.

A Redis build lock (R11a.01, 10-min TTL) serialises runs per config, and
a Redis snapshot of the prior subgraph is taken before Phase-1 so the
reconciler has something to restore.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.graphrag_ports import (
    BuildLockStore,
    DeltaMessage,
    Neo4jDriver,
    SnapshotStore,
    TripleExtractor,
)
from contexts.knowledge.domain.errors import (
    GraphRagBuildBusy,
    GraphRagBuildFailed,
)
from contexts.knowledge.domain.graphrag import (
    BuildResult,
    BuildState,
    GraphRagConfig,
    Triple,
)
from contexts.knowledge.infrastructure.graphrag_repositories import (
    GraphRagConfigRepository,
)
from contexts.knowledge.infrastructure.graphrag_vector_store import (
    GraphRagVectorStore,
)
from shared_kernel import audit

_log = logging.getLogger(__name__)

LOCK_TTL_S = 10 * 60  # R11a.01
SNAPSHOT_TTL_S = 24 * 60 * 60  # 24h — reconciler runs at 60s period


@dataclass(frozen=True, slots=True)
class EntityEmbedding:
    """Interim product of the embed step — surfaced for tests + workers."""

    point_id: uuid.UUID
    entity: str
    description: str
    vector: list[float]


class GraphRagBuilder:
    """Owns the full 2PC lifecycle for a single build."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        neo4j: Neo4jDriver,
        vector_store: GraphRagVectorStore,
        extractor: TripleExtractor,
        lock_store: BuildLockStore,
        snapshot_store: SnapshotStore,
        delta_loader: DeltaLoader,
        embedder_factory: EmbedderFactory,
    ) -> None:
        self._db = db
        self._neo4j = neo4j
        self._vectors = vector_store
        self._extractor = extractor
        self._locks = lock_store
        self._snapshots = snapshot_store
        self._delta_loader = delta_loader
        self._embedder_factory = embedder_factory
        self._configs = GraphRagConfigRepository(db)

    async def run(
        self,
        *,
        config_id: uuid.UUID,
        mode: Literal["delta", "full"] = "delta",
        triggered_by: str = "manual",
    ) -> BuildResult:
        cfg = await self._configs.get(config_id)
        if cfg is None:
            raise GraphRagBuildFailed(f"config {config_id} missing")
        if not await self._locks.acquire(config_id, ttl_s=LOCK_TTL_S):
            raise GraphRagBuildBusy(str(config_id))

        build_id = uuid.uuid4()
        try:
            return await self._run_locked(
                cfg=cfg,
                build_id=build_id,
                mode=mode,
                triggered_by=triggered_by,
            )
        finally:
            await self._locks.release(config_id)

    async def _run_locked(
        self,
        *,
        cfg: GraphRagConfig,
        build_id: uuid.UUID,
        mode: Literal["delta", "full"],
        triggered_by: str,
    ) -> BuildResult:
        # idle/failed → running. Anything else is a refusal.
        if cfg.last_build_state not in {
            BuildState.IDLE,
            BuildState.FAILED,
        }:
            raise GraphRagBuildBusy(
                f"config {cfg.id} in non-resumable state " f"{cfg.last_build_state.value}"
            )

        await self._configs.set_state(
            config_id=cfg.id,
            state=BuildState.RUNNING,
            error=None,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="graphrag.build_started",
                resource_type="graphrag_config",
                resource_id=cfg.id,
                metadata={
                    "build_id": str(build_id),
                    "mode": mode,
                    "triggered_by": triggered_by,
                },
            ),
        )

        # Snapshot the prior subgraph BEFORE we touch anything — so Phase-2
        # failure has something to roll back to.
        try:
            prior = await self._neo4j.snapshot_subgraph(
                config_id=cfg.id,
                build_id=None,
            )
            await self._snapshots.put(
                config_id=cfg.id,
                build_id=build_id,
                snapshot=prior,
                ttl_s=SNAPSHOT_TTL_S,
            )
        except Exception as exc:
            await self._fail_phase1(cfg.id, build_id, f"snapshot: {exc}")
            return BuildResult(
                config_id=cfg.id,
                build_id=build_id,
                state=BuildState.FAILED,
                triples_written=0,
                entities_written=0,
                error=str(exc),
            )

        # ------------ Phase 1: extract triples + upsert Neo4j -----------
        try:
            delta = await self._delta_loader.load(
                config_id=cfg.id,
                since=cfg.last_build_at,
                mode=mode,
            )
            triples = await self._extractor.extract(
                config_id=cfg.id,
                builder_key_group_id=cfg.builder_key_group_id,
                messages=delta,
            )
            n_triples = await self._neo4j.apply_triples(
                config_id=cfg.id,
                build_id=build_id,
                triples=triples,
            )
        except Exception as exc:
            await self._fail_phase1(cfg.id, build_id, str(exc))
            return BuildResult(
                config_id=cfg.id,
                build_id=build_id,
                state=BuildState.FAILED,
                triples_written=0,
                entities_written=0,
                error=str(exc),
            )

        await self._configs.set_state(
            config_id=cfg.id,
            state=BuildState.NEO4J_COMMITTED,
            error=None,
        )

        # ------------ Phase 2: embed + upsert Qdrant ---------------------
        try:
            embeddings = await self._embed_entities(
                cfg=cfg,
                triples=triples,
            )
            if embeddings:
                await self._vectors.ensure_graphrag_collection(
                    cfg.project_id,
                    vector_size=len(embeddings[0].vector),
                )
                await self._vectors.upsert_entities(
                    project_id=cfg.project_id,
                    build_id=build_id,
                    points=[(e.point_id, e.vector, e.entity, e.description) for e in embeddings],
                )
        except Exception as exc:
            # Phase-2 failure: go to failed_compensating; reconciler takes it.
            await self._configs.set_state(
                config_id=cfg.id,
                state=BuildState.FAILED_COMPENSATING,
                error=f"phase2: {exc}",
            )
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="graphrag.build_failed",
                    resource_type="graphrag_config",
                    resource_id=cfg.id,
                    metadata={
                        "build_id": str(build_id),
                        "phase": "qdrant",
                        "error": str(exc),
                    },
                ),
            )
            return BuildResult(
                config_id=cfg.id,
                build_id=build_id,
                state=BuildState.FAILED_COMPENSATING,
                triples_written=n_triples,
                entities_written=0,
                error=str(exc),
            )

        # Both phases committed → sweep old builds + finalise.
        await self._configs.set_state(
            config_id=cfg.id,
            state=BuildState.QDRANT_COMMITTED,
            error=None,
        )
        # Final idle state + stamp last_build_at.
        await self._configs.set_state(
            config_id=cfg.id,
            state=BuildState.IDLE,
            error=None,
            stamp_built_at=True,
        )
        await self._snapshots.delete(config_id=cfg.id, build_id=build_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="graphrag.build_finished",
                resource_type="graphrag_config",
                resource_id=cfg.id,
                metadata={
                    "build_id": str(build_id),
                    "triples": n_triples,
                    "entities": len(embeddings),
                },
            ),
        )
        return BuildResult(
            config_id=cfg.id,
            build_id=build_id,
            state=BuildState.IDLE,
            triples_written=n_triples,
            entities_written=len(embeddings),
            error=None,
        )

    async def _fail_phase1(
        self,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
        error: str,
    ) -> None:
        await self._configs.set_state(
            config_id=config_id,
            state=BuildState.FAILED,
            error=error,
        )
        await self._snapshots.delete(
            config_id=config_id,
            build_id=build_id,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="graphrag.build_failed",
                resource_type="graphrag_config",
                resource_id=config_id,
                metadata={
                    "build_id": str(build_id),
                    "phase": "neo4j",
                    "error": error,
                },
            ),
        )

    async def _embed_entities(
        self,
        *,
        cfg: GraphRagConfig,
        triples: Sequence[Triple],
    ) -> list[EntityEmbedding]:
        """Build a description per unique entity and embed them in a batch."""
        entities: dict[str, list[str]] = {}
        for tr in triples:
            entities.setdefault(tr.subject, []).append(f"{tr.subject} {tr.relation} {tr.object}")
            entities.setdefault(tr.object, []).append(f"{tr.subject} {tr.relation} {tr.object}")
        if not entities:
            return []
        ordered = sorted(entities.items())
        descriptions = [" | ".join(v) for _, v in ordered]
        embedder = await self._embedder_factory(cfg)
        vectors = await embedder.embed_batch(descriptions)
        return [
            EntityEmbedding(
                point_id=uuid.uuid4(),
                entity=entity,
                description=desc,
                vector=vec,
            )
            for (entity, _), desc, vec in zip(ordered, descriptions, vectors, strict=False)
        ]


# ---------------------------------------------------------------------------
# Collaborator protocols — declared here so the builder's import surface is
# self-contained without dragging concrete clients into the app layer.
# ---------------------------------------------------------------------------

from collections.abc import Awaitable, Callable  # noqa: E402
from typing import Protocol  # noqa: E402


class DeltaLoader(Protocol):
    async def load(
        self,
        *,
        config_id: uuid.UUID,
        since: Any,
        mode: Literal["delta", "full"],
    ) -> list[DeltaMessage]: ...


class _Embedder(Protocol):
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


EmbedderFactory = Callable[[GraphRagConfig], Awaitable[_Embedder]]


__all__ = [
    "DeltaLoader",
    "EmbedderFactory",
    "EntityEmbedding",
    "GraphRagBuilder",
    "LOCK_TTL_S",
    "SNAPSHOT_TTL_S",
]
