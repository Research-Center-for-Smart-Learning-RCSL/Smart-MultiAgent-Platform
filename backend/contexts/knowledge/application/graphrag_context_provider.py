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
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.graphrag_retrieve import EvidenceFetcher

_log = logging.getLogger(__name__)

# Default embedding model per provider for GraphRAG retrieval — mirrors the
# builder's map in ``app.workers.tasks.graphrag`` (kept local: contexts must
# not import from ``app``).
_GRAPHRAG_EMBED_MODELS: dict[str, str] = {
    "openai": "text-embedding-3-small",
    "gemini": "text-embedding-004",
    "voyage": "voyage-3",
}
_MAX_EVIDENCE_EXCERPTS = 10
_MAX_EVIDENCE_CHARS = 280


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
    ) -> str | None:
        """Return a formatted GraphRAG context string, or ``None`` if unavailable.

        Safe to call unconditionally — returns ``None`` when the config is
        missing, infrastructure is not configured, or retrieval fails.
        """
        queries = _normalise_queries(query_text=query_text, query_texts=query_texts)
        if graphrag_config_id is None or not queries:
            return None
        try:
            bundles = []
            for query in queries:
                bundle = await self._graphrag_query(graphrag_config_id, query)
                if bundle is not None:
                    bundles.append(bundle)
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

    # -- internal wiring ------------------------------------------------

    async def _graphrag_query(
        self,
        config_id: uuid.UUID,
        query: str,
    ) -> Any:
        """Production GraphRAG retrieval wiring (E.8).

        Seam for unit tests — fakes replace this method to exercise
        :meth:`query` without a live Neo4j/Qdrant stack.
        """
        from app.config.settings import get_settings

        settings = get_settings()
        neo4j_conf = getattr(settings, "neo4j", None)
        if neo4j_conf is None or self._qdrant_url is None:
            return None

        from qdrant_client import AsyncQdrantClient

        from contexts.knowledge.application.graphrag_retrieve import (
            GraphRagRetrieveService,
        )
        from contexts.knowledge.infrastructure.embedders import router_embedder_for
        from contexts.knowledge.infrastructure.graphrag_vector_store import (
            GraphRagVectorStore,
        )
        from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver

        async def _embedder_factory(cfg: Any) -> Any:
            resolved = await self._resolve_embed_key(cfg.builder_key_group_id)
            if resolved is None:
                raise RuntimeError(
                    f"builder key group {cfg.builder_key_group_id} has no embedding key",
                )
            provider, model, key_id = resolved
            return router_embedder_for(
                router=self._router,  # type: ignore[arg-type]
                key_id=key_id,
                provider=provider,
                model=model,
            )

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
                evidence_fetcher=self._evidence_fetcher,
            )
            return await svc.query(config_id=config_id, text=query)
        finally:
            await qclient.close()
            await driver.close()

    async def _resolve_embed_key(
        self,
        builder_key_group_id: uuid.UUID,
    ) -> tuple[str, str, uuid.UUID] | None:
        """First embedding-capable key in the builder group -> (provider, model, key_id).

        Mirrors the builder's resolution so retrieval embeds with the same
        model family the build used.
        """
        from contexts.keys.infrastructure.group_repository import (
            KeyGroupMemberRepository,
        )
        from contexts.keys.infrastructure.repositories import ApiKeyRepository

        members = await KeyGroupMemberRepository(self._db).list_ordered_carried(
            builder_key_group_id,
        )
        for m in members:
            key = await ApiKeyRepository(self._db).get_active(m.key_id)
            if key is None:
                continue
            provider = key.provider.value
            model = _GRAPHRAG_EMBED_MODELS.get(provider)
            if model is None:
                continue
            return provider, model, key.id
        return None


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
) -> EvidenceFetcher:
    """Return an EvidenceFetcher that formats conversation messages as excerpts.

    Callers supply ``get_message`` so this module stays free of conversation
    context imports.  Inject via ``GraphRagContextProvider(evidence_fetcher=...)``.
    """

    async def _fetch(ids: list[uuid.UUID]) -> list[str]:
        excerpts: list[str] = []
        for message_id in list(dict.fromkeys(ids))[:_MAX_EVIDENCE_EXCERPTS]:
            msg = await get_message(message_id)
            if msg is None:
                continue
            text = _compact_excerpt(msg.content_md)
            if not text:
                continue
            excerpts.append(f"{msg.sender_type.value}: {text}")
        return excerpts

    return _fetch


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
            if previous is None or rel.confidence > previous.confidence:
                relation_by_key[key] = rel
        for excerpt in bundle.evidence_excerpts:
            if excerpt not in evidence:
                evidence.append(excerpt)

    relations = sorted(relation_by_key.values(), key=lambda r: r.confidence, reverse=True)
    return GraphRagBundle(
        entities=tuple(entities),
        relations=tuple(relations),
        evidence_excerpts=tuple(evidence[:10]),
    )


__all__ = ["GraphRagContextProvider", "build_evidence_fetcher"]
