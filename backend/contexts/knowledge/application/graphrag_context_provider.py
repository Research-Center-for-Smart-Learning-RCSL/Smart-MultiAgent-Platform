"""GraphRAG context retrieval provider (R11.06).

Extracted from ``TurnEngine._graphrag_context``, ``_graphrag_query``, and
``_resolve_graphrag_embed_key`` so the agent-runtime context no longer
inlines Neo4j/Qdrant client construction or embedding-key resolution.

Lives in the *knowledge* context alongside ``GraphRagRetrieveService``.

Failure semantics: a retrieval failure must never fail the agent turn —
every exception is caught and logged, returning ``None``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.embed_resolution import resolve_pinned_embed_key
from contexts.knowledge.application.graphrag_retrieve import EvidenceFetcher

_log = logging.getLogger(__name__)

_MAX_EVIDENCE_EXCERPTS = 10
_MAX_EVIDENCE_CHARS = 280
# Scan bound: examine at most this many refs to find _MAX_EVIDENCE_EXCERPTS
# readable ones. Caps the fetcher's per-turn DB round-trips while leaving enough
# headroom to look past unreadable refs the room-ACL filter drops.
_MAX_EVIDENCE_CANDIDATES = 40


class _RoomMembershipCheck(Protocol):
    """The room read-ACL for an agent — matches ``ConversationFacade.is_agent_in_chatroom``."""

    async def __call__(self, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> bool: ...


class GraphRagContextProvider:
    """Produce a GraphRAG context block for an agent turn.

    Parameters match the ``TurnEngine`` constructor so the caller can build
    this once per engine instance and reuse it across turns.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        router: object,  # ProviderRouter — typed as object to avoid circular import
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        evidence_fetcher: EvidenceFetcher | None = None,
    ) -> None:
        self._db = db
        self._router = router
        self._qdrant_url = qdrant_url
        self._qdrant_api_key = qdrant_api_key
        self._evidence_fetcher = evidence_fetcher

    async def query(
        self,
        *,
        graphrag_config_id: uuid.UUID | None,
        query_text: str | None = None,
        query_texts: Sequence[str] | None = None,
        querying_agent_id: uuid.UUID | None = None,
    ) -> str | None:
        """Return a formatted GraphRAG context string, or ``None`` if unavailable.

        Safe to call unconditionally — returns ``None`` when the config is
        missing, infrastructure is not configured, or retrieval fails.

        ``querying_agent_id`` is the agent whose turn is retrieving; it is
        threaded to the evidence fetcher so excerpts from rooms the agent may
        not read are dropped (WS3 AC-7).
        """
        queries = _normalise_queries(query_text=query_text, query_texts=query_texts)
        if graphrag_config_id is None or not queries:
            return None
        try:
            bundles = await self._graphrag_query(graphrag_config_id, queries, querying_agent_id)
            bundle = _merge_bundles(bundles)
            if bundle is None or not (bundle.entities or bundle.relations):
                return None
            return str(bundle.as_system_message()["content"])
        except Exception:
            _log.warning(
                "GraphRAG retrieval failed config=%s",
                graphrag_config_id,
                exc_info=True,
            )
            return None

    async def query_layers(
        self,
        *,
        graphrag_config_ids: Sequence[uuid.UUID],
        query_text: str | None = None,
        query_texts: Sequence[str] | None = None,
        querying_agent_id: uuid.UUID | None = None,
    ) -> str | None:
        """Assemble a layered GraphRAG context, narrowest-first (WS4 R11.09).

        ``graphrag_config_ids`` are the covering configs ordered narrow -> wide
        (chatroom, then each enabled agent_group layer, then the enabled
        workspace). Each layer retrieves independently; a tiered merge fills the
        2 KB budget narrowest-first with entity/relation dedup keeping the
        narrowest occurrence, so a shared wide layer never dilutes the agent's
        own room memory. A single-layer call is byte-identical to :meth:`query`.

        Like :meth:`query`, any failure degrades to ``None`` — never a raised
        turn error.
        """
        queries = _normalise_queries(query_text=query_text, query_texts=query_texts)
        if not graphrag_config_ids or not queries:
            return None
        try:
            layer_bundles: list[Any] = []
            for config_id in graphrag_config_ids:
                # Per-layer retrieval, isolated: one failing layer degrades to
                # fewer layers rather than dropping the whole context. Clients
                # rebuild per layer, but the layer count is bounded (chatroom +
                # <=N groups + workspace) and each layer needs its own config
                # load + embedder anyway.
                try:
                    per_query = await self._graphrag_query(config_id, queries, querying_agent_id)
                except Exception:
                    _log.warning(
                        "GraphRAG layer retrieval failed config=%s",
                        config_id,
                        exc_info=True,
                    )
                    continue
                merged = _merge_bundles(per_query)
                if merged is not None and (merged.entities or merged.relations):
                    layer_bundles.append(merged)
            bundle = _merge_layers_tiered(layer_bundles)
            if bundle is None or not (bundle.entities or bundle.relations):
                return None
            return str(bundle.as_system_message()["content"])
        except Exception:
            _log.warning(
                "GraphRAG layered assembly failed layers=%s",
                list(graphrag_config_ids),
                exc_info=True,
            )
            return None

    # -- internal wiring ------------------------------------------------

    async def _graphrag_query(
        self,
        config_id: uuid.UUID,
        queries: Sequence[str],
        querying_agent_id: uuid.UUID | None = None,
    ) -> list[Any]:
        """Production GraphRAG retrieval wiring (E.8).

        Builds the Neo4j + Qdrant clients and resolves the embedding key once,
        then runs every query through a single retriever — the clients and key
        are invariant across the queries for one config, so per-query rebuilds
        would just repeat the connect/auth handshakes and the embed-key lookup.

        Seam for unit tests — fakes replace this method to exercise
        :meth:`query` without a live Neo4j/Qdrant stack.
        """
        from app.config.settings import get_settings

        settings = get_settings()
        neo4j_conf = getattr(settings, "neo4j", None)
        if neo4j_conf is None or self._qdrant_url is None:
            return []

        from qdrant_client import AsyncQdrantClient

        from contexts.knowledge.application.graphrag_retrieve import (
            GraphRagRetrieveService,
        )
        from contexts.knowledge.infrastructure.embedders import router_embedder_for
        from contexts.knowledge.infrastructure.graphrag_repositories import (
            GraphRagConfigRepository,
        )
        from contexts.knowledge.infrastructure.graphrag_vector_store import (
            GraphRagVectorStore,
        )
        from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver

        # Resolve+build the embedder once and reuse it for every query — the
        # key group and model are the same across a config's queries.
        embedder_cache: dict[uuid.UUID, Any] = {}

        async def _embedder_factory(cfg: Any) -> Any:
            cached = embedder_cache.get(cfg.id)
            if cached is not None:
                return cached
            # Phase 2a D2: the query MUST embed with the project's pinned provider
            # so its vector matches the collection's fixed dimension; the shared
            # resolver single-sources that invariant with the build path.
            provider, model, key_id = await resolve_pinned_embed_key(self._db, cfg)
            embedder = router_embedder_for(
                router=self._router,  # type: ignore[arg-type]
                key_id=key_id,
                provider=provider,
                model=model,
            )
            embedder_cache[cfg.id] = embedder
            return embedder

        driver = Neo4jAsyncDriver(
            uri=neo4j_conf.url,
            auth=(neo4j_conf.user, neo4j_conf.password),
        )
        qclient = AsyncQdrantClient(
            url=self._qdrant_url,
            api_key=self._qdrant_api_key or None,
        )
        try:
            svc = GraphRagRetrieveService(
                self._db,
                neo4j=driver,
                vector_store=GraphRagVectorStore(qclient),
                embedder_factory=_embedder_factory,
                configs=GraphRagConfigRepository(self._db),
                evidence_fetcher=self._evidence_fetcher,
                # WS5 (R11.21): the platform recency half-life default a config
                # inherits when it leaves recency_half_life_days NULL.
                default_half_life_days=settings.graphrag.recency_half_life_days_default,
            )
            bundles: list[Any] = []
            for query in queries:
                bundle = await svc.query(
                    config_id=config_id,
                    text=query,
                    querying_agent_id=querying_agent_id,
                )
                if bundle is not None:
                    bundles.append(bundle)
            return bundles
        finally:
            await qclient.close()
            await driver.close()


def _normalise_queries(*, query_text: str | None, query_texts: Sequence[str] | None) -> list[str]:
    queries: list[str] = []
    for raw in ([query_text] if query_text is not None else []) + list(query_texts or []):
        text = " ".join(str(raw or "").split())
        if text and text not in queries:
            queries.append(text)
    return queries


def _compact_excerpt(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= _MAX_EVIDENCE_CHARS:
        return compact
    return compact[: _MAX_EVIDENCE_CHARS - 3].rstrip() + "..."


def build_evidence_fetcher(
    get_message: Callable[[uuid.UUID], Awaitable[Any]],
    is_agent_in_chatroom: _RoomMembershipCheck,
) -> EvidenceFetcher:
    """Return an EvidenceFetcher that formats conversation messages as excerpts.

    Callers supply ``get_message`` and ``is_agent_in_chatroom`` so this module
    stays free of conversation context imports.  Inject via
    ``GraphRagContextProvider(evidence_fetcher=...)``.

    Privacy gate (WS3 AC-7): each excerpt's source message is dropped unless the
    querying agent participates in the message's chatroom. Evidence is always
    chatroom-sourced, so a missing querying agent fails closed — no excerpts.

    The room-ACL filter runs *before* the excerpt cap: it collects up to
    ``_MAX_EVIDENCE_EXCERPTS`` readable excerpts by scanning down the ref list
    (bounded by ``_MAX_EVIDENCE_CANDIDATES``), so unreadable refs at the top of
    the ranking no longer starve readable ones below them. Membership is
    memoized per chatroom so refs sharing a room cost a single ACL query.
    """

    async def _fetch(refs: list[str], querying_agent_id: uuid.UUID | None) -> list[str]:
        # No principal to authorize against — fail closed rather than leak raw
        # message content that the caller has not proven it may read.
        if querying_agent_id is None:
            return []
        room_readable: dict[uuid.UUID, bool] = {}
        excerpts: list[str] = []
        for ref in list(dict.fromkeys(refs))[:_MAX_EVIDENCE_CANDIDATES]:
            if len(excerpts) >= _MAX_EVIDENCE_EXCERPTS:
                break
            try:
                message_id = uuid.UUID(ref)
            except ValueError:
                continue
            msg = await get_message(message_id)
            if msg is None:
                continue
            readable = room_readable.get(msg.chatroom_id)
            if readable is None:
                readable = await is_agent_in_chatroom(
                    chatroom_id=msg.chatroom_id,
                    agent_id=querying_agent_id,
                )
                room_readable[msg.chatroom_id] = readable
            if not readable:
                continue
            text = _compact_excerpt(msg.content_md)
            if not text:
                continue
            excerpts.append(f"{msg.sender_type.value}: {text}")
        return excerpts

    return _fetch


def _rank(rel: Any) -> float:
    """A relation's ranking weight: its recency-weighted score, or raw confidence
    for a timeless edge that never got one (WS5)."""
    return float(rel.score if rel.score is not None else rel.confidence)


def _merge_bundles(bundles: Sequence[Any]) -> Any:
    if not bundles:
        return None
    from contexts.knowledge.domain.graphrag import GraphRagBundle

    entities: list[str] = []
    relation_by_key: dict[tuple[str, str, str], Any] = {}
    evidence: list[str] = []
    for bundle in bundles:
        for entity in bundle.entities:
            if entity not in entities:
                entities.append(entity)
        for rel in bundle.relations:
            key = (rel.subject, rel.relation, rel.object)
            previous = relation_by_key.get(key)
            # WS5: rank by the recency-weighted score when present, else raw
            # confidence — so the same edge keeps its temporally-decayed rank
            # across a config's per-query bundles.
            if previous is None or _rank(rel) > _rank(previous):
                relation_by_key[key] = rel
        for excerpt in bundle.evidence_excerpts:
            if excerpt not in evidence:
                evidence.append(excerpt)

    relations = sorted(relation_by_key.values(), key=_rank, reverse=True)
    return GraphRagBundle(
        entities=tuple(entities),
        relations=tuple(relations),
        evidence_excerpts=tuple(evidence[:10]),
    )


def _merge_layers_tiered(layer_bundles: Sequence[Any]) -> Any:
    """Tiered narrow->wide merge of per-layer bundles (WS4 R11.09).

    ``layer_bundles`` are ordered narrowest-first; each is already merged within
    its own layer (confidence-ranked by :func:`_merge_bundles`). Entities,
    relations, and evidence are concatenated in layer order with dedup keeping
    the NARROWEST occurrence (first wins) and are deliberately NOT re-sorted:
    the 2 KB render cap trims from the tail, so the widest layer's content is
    dropped first and the budget fills narrowest-first, each wider layer taking
    only the remainder. A single-layer input round-trips to an identical bundle.
    """
    if not layer_bundles:
        return None
    from contexts.knowledge.domain.graphrag import GraphRagBundle

    entities: list[str] = []
    seen_entities: set[str] = set()
    relations: list[Any] = []
    seen_relations: set[tuple[str, str, str]] = set()
    evidence: list[str] = []
    seen_evidence: set[str] = set()
    for bundle in layer_bundles:
        for entity in bundle.entities:
            if entity not in seen_entities:
                seen_entities.add(entity)
                entities.append(entity)
        for rel in bundle.relations:
            key = (rel.subject, rel.relation, rel.object)
            if key not in seen_relations:
                seen_relations.add(key)
                relations.append(rel)
        for excerpt in bundle.evidence_excerpts:
            if excerpt not in seen_evidence:
                seen_evidence.add(excerpt)
                evidence.append(excerpt)
    return GraphRagBundle(
        entities=tuple(entities),
        relations=tuple(relations),
        evidence_excerpts=tuple(evidence[:_MAX_EVIDENCE_EXCERPTS]),
    )


__all__ = ["GraphRagContextProvider", "build_evidence_fetcher"]
