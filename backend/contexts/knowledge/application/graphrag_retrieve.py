"""GraphRAG hybrid retrieval — vector + Neo4j traversal (E.8 / R11.06).

Pipeline:
  1. Embed the query text with the same BYO embedder the builder used.
  2. Vector-search the ``graphrag_{project_id}`` Qdrant collection →
     top-N candidate entities.
  3. Traverse 1–2 hops from those entities in Neo4j tagged with the
     config's current active build.
  4. Bundle entities + relations + evidence excerpts into a
     :class:`GraphRagBundle` that the conversation context injects as a
     ``{"type":"graphrag"}`` system message, capped at 2 KB.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.graphrag_ports import Neo4jDriver
from contexts.knowledge.domain.errors import GraphRagConfigNotFound
from contexts.knowledge.domain.graphrag import (
    GraphRagBundle,
    GraphRagConfig,
    RelationEdge,
)
from contexts.knowledge.infrastructure.graphrag_repositories import (
    GraphRagConfigRepository,
)
from contexts.knowledge.infrastructure.graphrag_vector_store import (
    GraphRagVectorStore,
)


class _Embedder:  # pragma: no cover — Protocol-ish duck typing
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...  # type: ignore[empty-body]


EmbedderFactory = Callable[[GraphRagConfig], Awaitable[_Embedder]]
EvidenceFetcher = Callable[[list[uuid.UUID]], Awaitable[list[str]]]


class GraphRagRetrieveService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        neo4j: Neo4jDriver,
        vector_store: GraphRagVectorStore,
        embedder_factory: EmbedderFactory,
        evidence_fetcher: EvidenceFetcher | None = None,
    ) -> None:
        self._db = db
        self._neo4j = neo4j
        self._vectors = vector_store
        self._embedder_factory = embedder_factory
        self._evidence_fetcher = evidence_fetcher
        self._configs = GraphRagConfigRepository(db)

    async def _load(self, config_id: uuid.UUID) -> GraphRagConfig:
        cfg = await self._configs.get(config_id)
        if cfg is None:
            raise GraphRagConfigNotFound(str(config_id))
        return cfg

    async def query(
        self,
        *,
        config_id: uuid.UUID,
        text: str,
        top_k: int = 5,
        hops: int = 2,
    ) -> GraphRagBundle:
        cfg = await self._load(config_id)

        embedder = await self._embedder_factory(cfg)
        vecs = await embedder.embed_batch([text])
        if not vecs:
            from contexts.knowledge.infrastructure.embedders import EmbeddingError

            raise EmbeddingError(0, "embedder returned no vectors for query")
        query_vec = vecs[0]

        # Scope the vector search to this config's entity points (DOM-2).
        # ``graphrag_{project_id}`` is shared by every config in the project;
        # without the ``config_id`` filter a query would vector-match — and
        # seed Neo4j traversal with — entities from sibling or deleted
        # configs. We deliberately do NOT filter by ``build_id``: the entity
        # collection accumulates across delta builds and each build embeds
        # only its own delta, so the live entity set spans many builds. The
        # builder instead supersedes stale per-entity duplicates on finalise
        # (DOM-8), keeping at most one point per entity name.
        hits = await self._vectors.search_entities(
            project_id=cfg.project_id,
            query_vector=query_vec,
            top_k=top_k,
            config_id=cfg.id,
        )
        if not hits:
            return GraphRagBundle(entities=(), relations=(), evidence_excerpts=())

        # Dedup by entity name — defends against any residual duplicate points
        # (DOM-8) so traversal seeds and the bundle stay distinct.
        seed_entities = list(dict.fromkeys(h.entity for h in hits if h.entity))
        raw_edges = await self._neo4j.traverse(
            config_id=cfg.id,
            seed_entities=seed_entities,
            hops=max(1, min(hops, 2)),
        )

        relations: list[RelationEdge] = []
        evidence_ids: list[uuid.UUID] = []
        for row in raw_edges:
            ev_raw = row.get("evidence_msg_ids") or []
            ev_ids: list[uuid.UUID] = []
            for v in ev_raw:
                try:
                    ev_ids.append(uuid.UUID(str(v)))
                except ValueError:
                    continue
            evidence_ids.extend(ev_ids)
            relations.append(
                RelationEdge(
                    subject=str(row.get("subject") or ""),
                    relation=str(row.get("relation") or ""),
                    object=str(row.get("object") or ""),
                    confidence=float(row.get("confidence") or 0.0),
                    evidence_msg_ids=tuple(ev_ids),
                )
            )

        excerpts: tuple[str, ...] = ()
        if self._evidence_fetcher is not None and evidence_ids:
            unique = list(dict.fromkeys(evidence_ids))[:10]
            excerpts = tuple(await self._evidence_fetcher(unique))

        return GraphRagBundle(
            entities=tuple(seed_entities),
            relations=tuple(relations),
            evidence_excerpts=excerpts,
        )


__all__ = ["EmbedderFactory", "EvidenceFetcher", "GraphRagRetrieveService"]
