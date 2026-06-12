"""GraphRAG reconciliation worker entrypoint (E.7 / R11.04).

Runs :class:`ReconciliationLoop.run_forever(period_s=60)` in-process.
Production deploys launch this under the same Arq supervisor as other
workers (operations.md §2.1); for local dev it is invocable as
``python -m app.workers.graphrag_reconciler``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config.settings import get_settings
from contexts.knowledge.application.graphrag_reconciler import (
    ReconciliationLoop,
)
from contexts.knowledge.infrastructure.graphrag_vector_store import (
    GraphRagVectorStore,
)
from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver
from contexts.knowledge.infrastructure.redis_lock import RedisSnapshotStore
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
        from contexts.keys.infrastructure.group_repository import (
            KeyGroupMemberRepository,
        )
        from contexts.keys.infrastructure.repositories import (
            ApiKeyRepository,
        )
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

        # Reconstruct entity descriptions (same algorithm as GraphRagBuilder._embed_entities).
        entities: dict[str, list[str]] = {}
        for row in rows:
            s, rel, o = row["subject"], row["relation"], row["object"]
            entities.setdefault(s, []).append(f"{s} {rel} {o}")
            entities.setdefault(o, []).append(f"{s} {rel} {o}")
        ordered = sorted(entities.items())
        descriptions = [" | ".join(v) for _, v in ordered]

        # Resolve the first embedding key from the builder key group.
        embed_model: dict[str, str] = {
            "openai": "text-embedding-3-small",
            "gemini": "text-embedding-004",
            "voyage": "voyage-3",
        }
        maker = get_sessionmaker()
        async with maker() as db:
            resolved: tuple[str, str, _uuid.UUID] | None = None
            members = await KeyGroupMemberRepository(db).list_ordered(
                cfg.builder_key_group_id,
            )
            for m in members:
                key = await ApiKeyRepository(db).get_active(m.key_id)
                if key is None:
                    continue
                prov = key.provider.value
                if prov not in embed_model:
                    continue
                resolved = (prov, embed_model[prov], key.id)
                break

            if resolved is None:
                raise RuntimeError(f"no embedding key in builder group {cfg.builder_key_group_id}")

            provider, model, key_id = resolved
            # Pinned-key embedder via the router — no plaintext here; usage is
            # accounted and committed with this session (R7.12).
            embedder = router_embedder_for(
                router=build_router(db),
                key_id=key_id,
                provider=provider,
                model=model,
            )
            vectors = await embedder.embed_batch(descriptions)
            await db.commit()

        if len(vectors) != len(descriptions):
            # DOM-5: a short embedding list would silently drop entities.
            # Raise so the reconciler counts this retry as failed.
            raise RuntimeError(
                f"embedder returned {len(vectors)} vectors for "
                f"{len(descriptions)} entities"
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
                (_uuid.uuid4(), vec, entity, desc)
                for (entity, _), desc, vec in zip(ordered, descriptions, vectors, strict=True)
            ],
        )

    return _retry


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    neo4j_conf = settings.neo4j
    neo4j = Neo4jAsyncDriver(
        uri=neo4j_conf.url,
        auth=(neo4j_conf.user, neo4j_conf.password),
    )
    from qdrant_client import AsyncQdrantClient

    qclient = AsyncQdrantClient(url=settings.qdrant.url)
    vectors = GraphRagVectorStore(qclient)
    snapshots = RedisSnapshotStore()

    maker = get_sessionmaker()
    loop = ReconciliationLoop(
        session_factory=lambda: maker(),
        neo4j=neo4j,
        vector_store=vectors,
        snapshot_store=snapshots,
        phase2_retry=_make_phase2_retry(neo4j, vectors),
    )
    try:
        await loop.run_forever(period_s=60.0)
    finally:
        await neo4j.close()


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()


__all__ = ["run"]
