"""Knowledge Map context retrieval provider (Phase 3, WS3; R11.14, R11.12, Q-2).

Mirrors :class:`GraphRagContextProvider` for the document Knowledge Map: it reuses
the shared Qdrant-seed + Neo4j-traverse engine (:class:`GraphRagRetrieveService`) on
the ``knowmap`` collection, then applies the per-Agent **allowlist edge filter** — the
security core (AC-4/AC-5):

- resolve the agent's readable ``knowmap_document`` ids once per turn;
- keep a relation only if **every** one of its source documents is readable
  (provenance decoded from the relation's opaque ``evidence_refs``);
- surface an entity only via a kept relation (never the raw Qdrant seed set, which
  would leak an entity name derived solely from a denied document);
- hydrate evidence excerpts from ``knowmap_chunks`` under the same allowed-doc set.

An empty allowlist grants nothing. Any retrieval failure degrades to ``None`` — never
a raised turn error.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.context_provider_text import (
    compact_excerpt,
    normalise_queries,
)
from contexts.knowledge.application.embed_resolution import resolve_pinned_embed_key
from contexts.knowledge.domain.graphrag import GraphRagBundle, RelationEdge, edge_rank
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapChunkRepository,
    KnowmapDocumentRepository,
)

_log = logging.getLogger(__name__)

_MAX_EVIDENCE_EXCERPTS = 10


def decode_ref_document_id(ref: str) -> uuid.UUID | None:
    """Decode a ``{knowmap_document_id}#{chunk_idx}`` evidence ref to its doc id.

    Returns ``None`` for an unparseable ref so the caller can fail closed — a ref
    whose provenance cannot be verified must never satisfy the allowlist."""
    doc_part = ref.split("#", 1)[0]
    try:
        return uuid.UUID(doc_part)
    except ValueError:
        return None


def decode_ref(ref: str) -> tuple[uuid.UUID, int] | None:
    """Decode a ``{knowmap_document_id}#{chunk_idx}`` ref to ``(doc_id, chunk_idx)``."""
    doc, sep, idx = ref.partition("#")
    if not sep:
        return None
    try:
        return uuid.UUID(doc), int(idx)
    except ValueError:
        return None


def relation_source_documents(rel: RelationEdge) -> set[uuid.UUID] | None:
    """The set of source ``knowmap_document`` ids for a relation, or ``None`` if any
    ref is unparseable (unverifiable provenance → the caller must drop the edge)."""
    docs: set[uuid.UUID] = set()
    for ref in rel.evidence_refs:
        d = decode_ref_document_id(ref)
        if d is None:
            return None
        docs.add(d)
    return docs


def filter_relations_by_allowlist(
    relations: Sequence[RelationEdge],
    allowed_document_ids: set[uuid.UUID],
) -> list[RelationEdge]:
    """Keep only relations whose **every** source document is in the allowlist (G4).

    A relation with no evidence refs, or with any unparseable ref, is dropped — its
    provenance cannot be confirmed, so surfacing it could leak a denied document's
    corroboration. This is the security core; kept purely functional for unit tests.
    """
    kept: list[RelationEdge] = []
    for rel in relations:
        docs = relation_source_documents(rel)
        if not docs:  # None (unparseable) or empty (no provenance) → deny
            continue
        if docs <= allowed_document_ids:
            kept.append(rel)
    return kept


def entities_from_relations(relations: Sequence[RelationEdge]) -> list[str]:
    """Distinct entity names surfaced by the kept relations, in first-seen order.

    An entity appears only via a visible relation (Q-2): never from the raw Qdrant
    seed set, which could include a name derived solely from a denied document."""
    seen: list[str] = []
    for rel in relations:
        for name in (rel.subject, rel.object):
            if name and name not in seen:
                seen.append(name)
    return seen


def _dedup_relations(relations: Sequence[RelationEdge]) -> list[RelationEdge]:
    by_key: dict[tuple[str, str, str], RelationEdge] = {}
    for rel in relations:
        key = (rel.subject, rel.relation, rel.object)
        prev = by_key.get(key)
        if prev is None or edge_rank(rel) > edge_rank(prev):
            by_key[key] = rel
    return sorted(by_key.values(), key=edge_rank, reverse=True)


class KnowledgeMapContextProvider:
    """Produce a Knowledge Map context block for an agent turn (a third Axis-1
    system block beside file-RAG, independent of any Concept Map)."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        router: object,  # ProviderRouter — typed as object to avoid a circular import
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
    ) -> None:
        self._db = db
        self._router = router
        self._qdrant_url = qdrant_url
        self._qdrant_api_key = qdrant_api_key

    async def query(
        self,
        *,
        knowmap_config_id: uuid.UUID | None,
        query_text: str | None = None,
        query_texts: Sequence[str] | None = None,
        querying_agent_id: uuid.UUID | None = None,
    ) -> str | None:
        """Return a formatted Knowledge Map context string, or ``None``.

        Safe to call unconditionally — returns ``None`` when the config is missing,
        infrastructure is not configured, the agent has no readable documents, or
        retrieval fails. ``querying_agent_id`` is required: with no agent principal
        the allowlist grants nothing (secure-by-default)."""
        queries = normalise_queries(query_text=query_text, query_texts=query_texts)
        if knowmap_config_id is None or not queries or querying_agent_id is None:
            return None
        try:
            # Resolve the readable documents once per turn. Empty → no access (AC-4).
            allowed = set(
                await KnowmapDocumentRepository(self._db).allowed_document_ids(
                    config_id=knowmap_config_id, agent_id=querying_agent_id
                )
            )
            if not allowed:
                return None

            raw_relations = await self._retrieve_relations(knowmap_config_id, queries)
            kept = filter_relations_by_allowlist(_dedup_relations(raw_relations), allowed)
            if not kept:
                return None

            entities = entities_from_relations(kept)
            excerpts = await self._hydrate_excerpts(kept, allowed)
            bundle = GraphRagBundle(
                entities=tuple(entities),
                relations=tuple(kept),
                evidence_excerpts=tuple(excerpts),
            )
            if not (bundle.entities or bundle.relations):
                return None
            return str(bundle.as_system_message()["content"])
        except Exception:
            _log.warning("Knowledge Map retrieval failed config=%s", knowmap_config_id, exc_info=True)
            return None

    async def _hydrate_excerpts(
        self, relations: Sequence[RelationEdge], allowed: set[uuid.UUID]
    ) -> list[str]:
        refs: list[tuple[uuid.UUID, int]] = []
        for rel in relations:
            for raw in rel.evidence_refs:
                decoded = decode_ref(raw)
                if decoded is not None and decoded not in refs:
                    refs.append(decoded)
        if not refs:
            return []
        excerpt_map = await KnowmapChunkRepository(self._db).get_excerpts(
            refs, allowed_document_ids=list(allowed)
        )
        out: list[str] = []
        for ref in refs:
            if len(out) >= _MAX_EVIDENCE_EXCERPTS:
                break
            text = excerpt_map.get(ref)
            if text:
                compact = compact_excerpt(text)
                if compact and compact not in out:
                    out.append(compact)
        return out

    async def _retrieve_relations(self, config_id: uuid.UUID, queries: Sequence[str]) -> list[RelationEdge]:
        """Production wiring: Qdrant seed + Neo4j traverse on the ``knowmap``
        collection, returning the raw (unfiltered) relations across all queries.
        The evidence fetcher is left off — knowmap hydrates excerpts from
        ``knowmap_chunks`` after the allowlist edge filter, never from conversation.

        Seam for unit tests — fakes replace this to exercise :meth:`query` and the
        edge filter without a live Neo4j/Qdrant stack."""
        from app.config.settings import get_settings

        settings = get_settings()
        neo4j_conf = getattr(settings, "neo4j", None)
        if neo4j_conf is None or self._qdrant_url is None:
            return []

        from qdrant_client import AsyncQdrantClient

        from contexts.knowledge.application.graphrag_retrieve import GraphRagRetrieveService
        from contexts.knowledge.infrastructure.embedders import router_embedder_for
        from contexts.knowledge.infrastructure.graphrag_vector_store import GraphRagVectorStore
        from contexts.knowledge.infrastructure.knowmap_repositories import KnowmapConfigRepository
        from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver

        embedder_cache: dict[uuid.UUID, Any] = {}

        async def _embedder_factory(cfg: Any) -> Any:
            cached = embedder_cache.get(cfg.id)
            if cached is not None:
                return cached
            provider, model, key_id = await resolve_pinned_embed_key(self._db, cfg)
            embedder = router_embedder_for(
                router=self._router,  # type: ignore[arg-type]
                key_id=key_id,
                project_id=cfg.project_id,
                provider=provider,
                model=model,
            )
            embedder_cache[cfg.id] = embedder
            return embedder

        driver = Neo4jAsyncDriver(uri=neo4j_conf.url, auth=(neo4j_conf.user, neo4j_conf.password))
        qclient = AsyncQdrantClient(url=self._qdrant_url, api_key=self._qdrant_api_key or None)
        try:
            svc = GraphRagRetrieveService(
                self._db,
                neo4j=driver,
                vector_store=GraphRagVectorStore(qclient, prefix="knowmap"),
                embedder_factory=_embedder_factory,
                configs=KnowmapConfigRepository(self._db),
                evidence_fetcher=None,
                default_half_life_days=None,  # non-temporal (R11.21)
            )
            relations: list[RelationEdge] = []
            for query in queries:
                bundle = await svc.query(config_id=config_id, text=query, querying_agent_id=None)
                if bundle is not None:
                    relations.extend(bundle.relations)
            return relations
        finally:
            await qclient.close()
            await driver.close()


__all__ = [
    "KnowledgeMapContextProvider",
    "decode_ref",
    "decode_ref_document_id",
    "entities_from_relations",
    "filter_relations_by_allowlist",
    "relation_source_documents",
]
