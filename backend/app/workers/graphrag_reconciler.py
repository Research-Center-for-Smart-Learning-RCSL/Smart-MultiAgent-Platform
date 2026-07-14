"""GraphRAG reconciliation worker (E.7 / R11.04).

Production runs reconciliation as a once-per-minute arq cron — the
``graphrag_reconcile`` task in ``app.workers.tasks.graphrag`` calls
:func:`reconcile_once`, and arq's cron lock keeps it singleton across worker
replicas (registered in ``app.workers.main.WorkerSettings``).

It is also invocable as a standalone process (``python -m
app.workers.graphrag_reconciler``) for local dev or a dedicated worker; that loop
calls the same :func:`reconcile_once`, and the arq cron is the deployed path.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.config.settings import get_settings
from contexts.knowledge.application.embed_resolution import resolve_pinned_embed_key
from contexts.knowledge.application.graphrag_reconciler import (
    GraphConsumer,
    ReconciliationLoop,
)
from contexts.knowledge.infrastructure.channels import graphrag_channel
from contexts.knowledge.infrastructure.graphrag_repositories import (
    GraphRagConfigRepository,
)
from contexts.knowledge.infrastructure.graphrag_vector_store import (
    GraphRagVectorStore,
)
from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver
from contexts.knowledge.infrastructure.redis_lock import (
    RedisBuildLockStore,
    RedisSnapshotStore,
)
from shared_kernel.db.session import get_sessionmaker

_log = logging.getLogger(__name__)


def _make_phase2_retry(
    neo4j: Neo4jAsyncDriver,
    vector_store: GraphRagVectorStore,
) -> Any:
    """Return a Phase2Retry that re-embeds Phase-1 entities and upserts Qdrant."""
    import uuid as _uuid

    async def _retry(*, cfg: Any, build_id: Any) -> None:
        from contexts.keys.infrastructure.adapters import build_router
        from contexts.knowledge.infrastructure.embedders import (
            router_embedder_for,
        )

        # Get the triples written in Phase-1 so we can reconstruct descriptions.
        rows = await neo4j.list_triples_for_build(
            config_id=cfg.id,
            build_id=build_id,
        )
        if not rows:
            # Nothing was committed to Neo4j — Qdrant is already consistent.
            return

        # Reconstruct entity descriptions via the shared builder helper so a
        # recovered build re-embeds with exactly the original descriptions
        # (audit review #8).
        from contexts.knowledge.application.graphrag_builder import (
            build_entity_descriptions,
        )

        pairs = build_entity_descriptions((row["subject"], row["relation"], row["object"]) for row in rows)
        descriptions = [desc for _, desc in pairs]

        maker = get_sessionmaker()
        async with maker() as db:
            # D2: recover with the project's pinned embedding provider/model, not
            # the first carried key — otherwise a builder-group key swap between
            # the original build and the recovery would re-embed at a different
            # dimension and mis-index. Shared resolver keeps this in lockstep with
            # the build/retrieval paths.
            provider, model, key_id = await resolve_pinned_embed_key(db, cfg)
            # Pinned-key embedder via the router — no plaintext here; usage is
            # accounted and committed with this session (R7.12).
            embedder = router_embedder_for(
                router=build_router(db),
                key_id=key_id,
                project_id=cfg.project_id,
                provider=provider,
                model=model,
            )
            vectors = await embedder.embed_batch(descriptions)
            await db.commit()

        if len(vectors) != len(descriptions):
            # DOM-5: a short embedding list would silently drop entities.
            # Raise so the reconciler counts this retry as failed.
            raise RuntimeError(
                f"embedder returned {len(vectors)} vectors for " f"{len(descriptions)} entities"
            )

        await vector_store.ensure_graphrag_collection(
            cfg.project_id,
            vector_size=len(vectors[0]),
        )
        await vector_store.upsert_entities(
            project_id=cfg.project_id,
            config_id=cfg.id,
            build_id=build_id,
            points=[
                (_uuid.uuid4(), vec, entity, desc) for (entity, desc), vec in zip(pairs, vectors, strict=True)
            ],
        )

    return _retry


@asynccontextmanager
async def _loop() -> AsyncIterator[ReconciliationLoop]:
    """Build a reconciliation loop and tear down BOTH its Neo4j AND Qdrant
    clients on exit. (The cron tick runs once per minute, so a leaked Qdrant
    client per tick would exhaust the worker's sockets — close both.)"""
    settings = get_settings()
    neo4j = Neo4jAsyncDriver(
        uri=settings.neo4j.url,
        auth=(settings.neo4j.user, settings.neo4j.password),
    )
    from qdrant_client import AsyncQdrantClient

    qclient = AsyncQdrantClient(url=settings.qdrant.url)
    vectors = GraphRagVectorStore(qclient)
    # Phase 3: the Knowledge Map shares this Neo4j node space (subgraphs keyed only
    # by config id). Both graph consumers are registered so the single sweep unions
    # their live ids, attributes each orphan to its owning table, and purges the
    # right Qdrant collection.
    from contexts.knowledge.infrastructure.knowmap_repositories import (
        KnowmapConfigRepository,
    )

    knowmap_vectors = GraphRagVectorStore(qclient, prefix="knowmap")
    maker = get_sessionmaker()
    loop = ReconciliationLoop(
        session_factory=lambda: maker(),
        repo_factory=GraphRagConfigRepository,
        neo4j=neo4j,
        vector_store=vectors,
        snapshot_store=RedisSnapshotStore(),
        phase2_retry=_make_phase2_retry(neo4j, vectors),
        lock_store=RedisBuildLockStore(),
        sweep_consumers=[
            GraphConsumer(
                repo_factory=GraphRagConfigRepository,
                vector_store=vectors,
                resource_type="graphrag_config",
            ),
            GraphConsumer(
                repo_factory=KnowmapConfigRepository,
                vector_store=knowmap_vectors,
                resource_type="knowmap_config",
            ),
        ],
        channel_fn=graphrag_channel,
    )
    try:
        yield loop
    finally:
        await neo4j.close()
        await qclient.close()


@asynccontextmanager
async def _knowmap_loop() -> AsyncIterator[ReconciliationLoop]:
    """Reconciliation loop for Knowledge Map builds (Phase 3, R11.04 reuse).

    Heals knowmap configs stuck in 2PC compensation, re-embedding into the
    ``knowmap`` collection. Registers no ``sweep_consumers`` — the primary graphrag
    loop's sweep is consumer-aware and reclaims orphans across both collections, so
    a second sweep would only race it."""
    settings = get_settings()
    neo4j = Neo4jAsyncDriver(uri=settings.neo4j.url, auth=(settings.neo4j.user, settings.neo4j.password))
    from qdrant_client import AsyncQdrantClient

    from contexts.knowledge.infrastructure.channels import knowmap_channel
    from contexts.knowledge.infrastructure.knowmap_repositories import (
        KnowmapConfigRepository,
    )

    qclient = AsyncQdrantClient(url=settings.qdrant.url)
    vectors = GraphRagVectorStore(qclient, prefix="knowmap")
    maker = get_sessionmaker()
    loop = ReconciliationLoop(
        session_factory=lambda: maker(),
        repo_factory=KnowmapConfigRepository,
        neo4j=neo4j,
        vector_store=vectors,
        snapshot_store=RedisSnapshotStore(),
        phase2_retry=_make_phase2_retry(neo4j, vectors),
        lock_store=RedisBuildLockStore(),
        channel_fn=knowmap_channel,
        resource_type="knowmap_config",
    )
    try:
        yield loop
    finally:
        await neo4j.close()
        await qclient.close()


async def reconcile_once() -> list[uuid.UUID]:
    """One scan-and-heal pass; builds deps, runs, tears down. Both the arq cron
    tick (M.5.4) and the standalone ``_main`` loop below call this.

    Runs the graphrag heal + (consumer-aware) orphan sweep, then a knowmap heal
    pass over the shared 2PC engine."""
    healed: list[uuid.UUID] = []
    async with _loop() as loop:
        healed.extend(await loop.run_once())
    async with _knowmap_loop() as kloop:
        healed.extend(await kloop.run_once())
    return healed


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    # Run the same composed pass as the arq cron (graphrag heal + consumer-aware
    # sweep, then the knowmap heal) so the standalone process heals every consumer —
    # not just graphrag, which would leave knowmap configs stuck mid-2PC forever.
    while True:
        try:
            await reconcile_once()
        except Exception:
            _log.exception("graphrag reconciler iteration failed")
        await asyncio.sleep(60.0)


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()


__all__ = ["run"]
