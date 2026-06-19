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
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger(__name__)

# Default embedding model per provider for GraphRAG retrieval — mirrors the
# builder's map in ``app.workers.tasks.graphrag`` (kept local: contexts must
# not import from ``app``).
_GRAPHRAG_EMBED_MODELS: dict[str, str] = {
    "openai": "text-embedding-3-small",
    "gemini": "text-embedding-004",
    "voyage": "voyage-3",
}


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
    ) -> None:
        self._db = db
        self._router = router
        self._qdrant_url = qdrant_url
        self._qdrant_api_key = qdrant_api_key

    async def query(
        self,
        *,
        graphrag_config_id: uuid.UUID | None,
        query_text: str | None,
    ) -> str | None:
        """Return a formatted GraphRAG context string, or ``None`` if unavailable.

        Safe to call unconditionally — returns ``None`` when the config is
        missing, infrastructure is not configured, or retrieval fails.
        """
        if graphrag_config_id is None or not query_text:
            return None
        try:
            bundle = await self._graphrag_query(graphrag_config_id, query_text)
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

        members = await KeyGroupMemberRepository(self._db).list_ordered(
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


__all__ = ["GraphRagContextProvider"]
