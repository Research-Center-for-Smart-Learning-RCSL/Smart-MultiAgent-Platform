"""GraphRAG reconciliation worker entrypoint (E.7 / R11.04).

Runs :class:`ReconciliationLoop.run_forever(period_s=60)` in-process.
Production deploys launch this under the same Arq supervisor as other
workers (operations.md §2.1); for local dev it is invocable as
``python -m app.workers.graphrag_reconciler``.
"""

from __future__ import annotations

import asyncio
import logging

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
):
    """Return a Phase2Retry that re-embeds Phase-1 entities and upserts Qdrant."""
    import uuid as _uuid

    async def _retry(*, cfg, build_id) -> None:  # noqa: ANN001
        from contexts.knowledge.infrastructure.embedders import (  # noqa: PLC0415
            embedder_for,
        )
        from contexts.keys.infrastructure.group_repository import (  # noqa: PLC0415
            KeyGroupMemberRepository,
        )
        from contexts.keys.infrastructure.repositories import (  # noqa: PLC0415
            ApiKeyRepository,
        )
        from contexts.keys.interfaces.facade import KeysFacade  # noqa: PLC0415

        # Get the triples written in Phase-1 so we can reconstruct descriptions.
        rows = await neo4j.list_triples_for_build(
            config_id=cfg.id, build_id=build_id,
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
        _EMBED_MODEL: dict[str, str] = {
            "openai": "text-embedding-3-small",
            "gemini": "text-embedding-004",
            "voyage": "voyage-3",
        }
        maker = get_sessionmaker()
        api_key_str: str | None = None
        provider = model = ""
        async with maker() as db:
            members = await KeyGroupMemberRepository(db).list_ordered(
                cfg.builder_key_group_id,
            )
            for m in members:
                key = await ApiKeyRepository(db).get_active(m.key_id)
                if key is None:
                    continue
                prov = key.provider.value
                if prov not in _EMBED_MODEL:
                    continue
                plaintext = await KeysFacade(db).unwrap_api_key_plaintext(key.id)
                try:
                    api_key_str = plaintext.decode("utf-8")
                    provider, model = prov, _EMBED_MODEL[prov]
                finally:
                    plaintext = b"\x00" * len(plaintext)  # noqa: F841
                break

        if api_key_str is None:
            raise RuntimeError(
                f"no embedding key in builder group {cfg.builder_key_group_id}"
            )

        embedder = embedder_for(provider=provider, model=model, api_key=api_key_str)
        vectors = await embedder.embed_batch(descriptions)

        await vector_store.ensure_graphrag_collection(
            cfg.project_id, vector_size=len(vectors[0]),
        )
        await vector_store.upsert_entities(
            project_id=cfg.project_id,
            build_id=build_id,
            points=[
                (_uuid.uuid4(), vec, entity, desc)
                for (entity, _), desc, vec in zip(ordered, descriptions, vectors)
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
