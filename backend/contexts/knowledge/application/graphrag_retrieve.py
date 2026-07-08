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

from contexts.knowledge.application.graphrag_ports import (
    ConfigLike,
    GraphRagConfigRepositoryPort,
    Neo4jDriver,
)
from contexts.knowledge.domain.errors import GraphRagConfigNotFound
from contexts.knowledge.domain.graphrag import (
    GraphRagBundle,
    RelationEdge,
    edge_rank,
    normalize_evidence_refs,
    recency_weighted_score,
)
from contexts.knowledge.infrastructure.graphrag_vector_store import (
    GraphRagVectorStore,
)
from shared_kernel.auth.clients import now


def _as_epoch(value: object) -> float | None:
    """Coerce a Neo4j-returned timestamp (numeric or None) to epoch seconds."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class _Embedder:  # pragma: no cover — Protocol-ish duck typing
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...  # type: ignore[empty-body]


EmbedderFactory = Callable[[ConfigLike], Awaitable[_Embedder]]
# Phase 2b WS3 (AC-7): the fetcher takes the querying agent id so it can drop
# excerpts sourced from rooms that agent may not read. ``None`` means no agent
# principal is in scope — the fetcher fails closed (returns nothing).
EvidenceFetcher = Callable[[list[str], uuid.UUID | None], Awaitable[list[str]]]


class GraphRagRetrieveService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        neo4j: Neo4jDriver,
        vector_store: GraphRagVectorStore,
        embedder_factory: EmbedderFactory,
        configs: GraphRagConfigRepositoryPort,
        evidence_fetcher: EvidenceFetcher | None = None,
        default_half_life_days: float | None = None,
    ) -> None:
        self._db = db
        self._neo4j = neo4j
        self._vectors = vector_store
        self._embedder_factory = embedder_factory
        self._evidence_fetcher = evidence_fetcher
        # WS5 (R11.21): platform fallback when a config leaves recency_half_life_days
        # NULL. ``None`` (the unit-test default) disables decay -> pure confidence.
        self._default_half_life_days = default_half_life_days
        self._configs = configs

    async def _load(self, config_id: uuid.UUID) -> ConfigLike:
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
        querying_agent_id: uuid.UUID | None = None,
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

        # WS5 (R11.21): the config's half-life, or the platform default when NULL.
        half_life = cfg.recency_half_life_days
        if half_life is None:
            half_life = self._default_half_life_days
        now_epoch = now().timestamp()

        relations: list[RelationEdge] = []
        evidence_refs: list[str] = []
        for row in raw_edges:
            refs = normalize_evidence_refs(row.get("evidence_msg_ids"))
            evidence_refs.extend(refs)
            confidence = float(row.get("confidence") or 0.0)
            last_seen_at = _as_epoch(row.get("last_seen_at"))
            relations.append(
                RelationEdge(
                    subject=str(row.get("subject") or ""),
                    relation=str(row.get("relation") or ""),
                    object=str(row.get("object") or ""),
                    confidence=confidence,
                    evidence_refs=refs,
                    # Phase 2b (R11.22): carry edge provenance through so the WS4
                    # layered/member-scoped retrieval can filter on it. Reuse the
                    # opaque-ref normaliser (uuid strings, length-bounded).
                    source_member_ids=normalize_evidence_refs(row.get("source_member_ids")),
                    # WS5 (R11.21): carry the temporal stamps and the recency-
                    # weighted rank so a recent low-confidence edge can outrank a
                    # stale high-confidence one under a short half-life (AC-9).
                    first_seen_at=_as_epoch(row.get("first_seen_at")),
                    last_seen_at=last_seen_at,
                    score=recency_weighted_score(
                        confidence=confidence,
                        last_seen_at=last_seen_at,
                        half_life_days=half_life,
                        now=now_epoch,
                    ),
                )
            )

        excerpts: tuple[str, ...] = ()
        if self._evidence_fetcher is not None and evidence_refs:
            # Pass the full deduped ref list (not a pre-capped slice) with the
            # querying agent so the fetcher applies the room read-ACL (WS3 AC-7)
            # and then caps — otherwise unreadable refs at the top would consume
            # the excerpt budget and starve readable refs below them.
            unique = list(dict.fromkeys(evidence_refs))
            excerpts = tuple(await self._evidence_fetcher(unique, querying_agent_id))

        # Audit M5 / WS5: order by the recency-weighted score (falling back to raw
        # confidence for a timeless edge) so the 2 KB bundle cap trims the weakest
        # edges, and recent edges float up under temporal decay.
        relations.sort(key=edge_rank, reverse=True)

        return GraphRagBundle(
            entities=tuple(seed_entities),
            relations=tuple(relations),
            evidence_excerpts=excerpts,
        )


__all__ = ["EmbedderFactory", "EvidenceFetcher", "GraphRagRetrieveService"]
