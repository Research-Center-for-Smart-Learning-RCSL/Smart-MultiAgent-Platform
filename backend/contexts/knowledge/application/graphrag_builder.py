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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.graphrag_events import publish_build_state
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

# Audit M7: cap how many relation fragments contribute to one entity's
# embedding description. A hot entity that appears in hundreds of triples would
# otherwise build a description that exceeds the embedder's input-token limit
# and fail Phase-2 on every reconciler retry. Bounding it keeps the description
# representative without unbounded growth.
MAX_DESC_FRAGMENTS = 40


def build_entity_descriptions(
    triples: Iterable[tuple[str, str, str]],
) -> list[tuple[str, str]]:
    """Map (subject, relation, object) triples to sorted (entity, description) pairs.

    Audit review #8: single source of truth shared by the builder's Phase-2 and
    the reconciler's Phase-2 retry, so a recovered build re-embeds entities with
    exactly the same description (and therefore comparable vectors) as the
    original build. Each entity's description is the ``" | "``-joined relation
    fragments it participates in, capped at :data:`MAX_DESC_FRAGMENTS`.
    """
    entities: dict[str, list[str]] = {}
    for subject, relation, obj in triples:
        fragment = f"{subject} {relation} {obj}"
        entities.setdefault(subject, []).append(fragment)
        entities.setdefault(obj, []).append(fragment)
    return [(name, " | ".join(frags[:MAX_DESC_FRAGMENTS])) for name, frags in sorted(entities.items())]


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
        await publish_build_state(cfg.id, BuildState.RUNNING.value, build_id=build_id)
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
        # Audit C1: persist each transition durably. The worker only committed
        # once after run() returned, collapsing RUNNING/NEO4J_COMMITTED into the
        # terminal state — so a crash mid-build left Postgres rolled back while
        # Neo4j kept the orphan triples, invisible to the reconciler (C2).
        await self._db.commit()

        # Snapshot the prior subgraph BEFORE we touch anything — so Phase-2
        # failure has something to roll back to. The current-build pointer is
        # the authoritative record of the in-flight build id the reconciler
        # reads (audit C4) instead of guessing from a key scan.
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
            await self._snapshots.set_current(
                config_id=cfg.id,
                build_id=build_id,
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
            # Audit M6: a "full" rebuild must re-extract the whole history, not
            # just messages newer than the last build. Pass since=None so the
            # loader scans from the beginning.
            since = None if mode == "full" else cfg.last_build_at
            delta = await self._delta_loader.load(
                config_id=cfg.id,
                since=since,
                mode=mode,
            )
            triples = await self._extractor.extract(
                config_id=cfg.id,
                builder_key_group_id=cfg.builder_key_group_id,
                messages=delta,
            )
            # Audit review #3: extraction can be slow; refresh the lock and bail
            # before touching Neo4j if we've lost it (another build may have
            # taken over after a TTL overrun) so we never write concurrently.
            if not await self._locks.refresh(cfg.id, ttl_s=LOCK_TTL_S):
                raise GraphRagBuildBusy(f"lock lost during phase-1 for {cfg.id}")
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
        await publish_build_state(cfg.id, BuildState.NEO4J_COMMITTED.value, build_id=build_id)
        # Audit C1/C2: make NEO4J_COMMITTED durable so a crash before Phase-2
        # finishes leaves a row the reconciler can pick up and heal.
        await self._db.commit()

        # ------------ Phase 2: embed + upsert Qdrant ---------------------
        try:
            embeddings = await self._embed_entities(
                cfg=cfg,
                triples=triples,
            )
            if embeddings:
                # Audit review #3: embedding can be slow too — refresh + verify
                # ownership before the Qdrant write.
                if not await self._locks.refresh(cfg.id, ttl_s=LOCK_TTL_S):
                    raise GraphRagBuildFailed(f"lock lost before qdrant upsert for {cfg.id}")
                await self._vectors.ensure_graphrag_collection(
                    cfg.project_id,
                    vector_size=len(embeddings[0].vector),
                )
                await self._vectors.upsert_entities(
                    project_id=cfg.project_id,
                    config_id=cfg.id,
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
            await publish_build_state(cfg.id, BuildState.FAILED_COMPENSATING.value, build_id=build_id)
            # Audit C1/C2: persist FAILED_COMPENSATING durably; the current-build
            # pointer is intentionally left set so the reconciler resolves this
            # build's id and retries Phase-2 (or rolls back).
            await self._db.commit()
            return BuildResult(
                config_id=cfg.id,
                build_id=build_id,
                state=BuildState.FAILED_COMPENSATING,
                triples_written=n_triples,
                entities_written=0,
                error=str(exc),
            )

        # Both phases committed → supersede stale duplicates + finalise.
        await self._configs.set_state(
            config_id=cfg.id,
            state=BuildState.QDRANT_COMMITTED,
            error=None,
        )
        # DOM-8: the GraphRAG entity collection accumulates across delta
        # builds — each build embeds only the entities in its own delta, so
        # earlier builds' points for *untouched* entities are still live and
        # MUST be kept. Only the points this build re-embedded supersede an
        # older copy: delete prior-build points for exactly those entity
        # names. Best-effort — the build has already succeeded, so a sweep
        # failure is logged, not fatal.
        if embeddings:
            try:
                await self._vectors.delete_superseded_entities(
                    project_id=cfg.project_id,
                    config_id=cfg.id,
                    keep_build_id=build_id,
                    entities=[e.entity for e in embeddings],
                )
            except Exception as exc:  # best-effort cleanup; never fail the build
                _log.warning(
                    "graphrag superseded-entity sweep failed for config %s " "build %s: %s",
                    cfg.id,
                    build_id,
                    exc,
                )
        # Final idle state + stamp last_build_at.
        await self._configs.set_state(
            config_id=cfg.id,
            state=BuildState.IDLE,
            error=None,
            stamp_built_at=True,
        )
        # Audit review #2: make the terminal IDLE durable BEFORE dropping the
        # Redis snapshot + current-build pointer. Otherwise a crash between the
        # cleanup and the worker's outer commit would roll Postgres back to
        # NEO4J_COMMITTED with the snapshot/pointer already gone — and the
        # reconciler would mark a genuinely-finished build FAILED.
        await self._db.commit()
        await self._snapshots.delete(config_id=cfg.id, build_id=build_id)
        await self._snapshots.clear_current(config_id=cfg.id)
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
        await publish_build_state(
            cfg.id,
            BuildState.IDLE.value,
            build_id=build_id,
            triples=n_triples,
            entities=len(embeddings),
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
        await self._snapshots.clear_current(config_id=config_id)
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
        await publish_build_state(config_id, BuildState.FAILED.value, build_id=build_id)
        # Audit C1: FAILED is a terminal state — make it durable immediately.
        await self._db.commit()

    async def _embed_entities(
        self,
        *,
        cfg: GraphRagConfig,
        triples: Sequence[Triple],
    ) -> list[EntityEmbedding]:
        """Build a description per unique entity and embed them in a batch."""
        pairs = build_entity_descriptions((t.subject, t.relation, t.object) for t in triples)
        if not pairs:
            return []
        descriptions = [desc for _, desc in pairs]
        embedder = await self._embedder_factory(cfg)
        vectors = await embedder.embed_batch(descriptions)
        if len(vectors) != len(descriptions):
            # DOM-5: a short embedding list would silently drop entities —
            # description rows with no Qdrant vector. A `strict=False` zip
            # would stop short and under-report `entities_written`. Fail
            # the build instead so the reconciler/operator sees it.
            raise GraphRagBuildFailed(
                f"embedder returned {len(vectors)} vectors for " f"{len(descriptions)} entities"
            )
        return [
            EntityEmbedding(
                point_id=uuid.uuid4(),
                entity=entity,
                description=desc,
                vector=vec,
            )
            for (entity, desc), vec in zip(pairs, vectors, strict=True)
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
    "MAX_DESC_FRAGMENTS",
    "SNAPSHOT_TTL_S",
    "build_entity_descriptions",
]
