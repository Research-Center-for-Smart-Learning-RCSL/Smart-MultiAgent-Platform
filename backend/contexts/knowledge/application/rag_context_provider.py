"""RAG context retrieval provider — encapsulates embedder/Qdrant/reranker wiring.

Extracted from ``TurnEngine._rag_context`` so the agent-runtime context no
longer inlines Qdrant client construction, embedder resolution, or reranker
assembly.  The provider lives in the *knowledge* context (where ``RetrieveService``
and ``QdrantStore`` already live) and exposes a single ``query()`` method that
returns a formatted context string or ``None``.

Failure semantics: a retrieval failure must never fail the agent turn — every
exception is caught and logged, returning ``None``.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger(__name__)


class RagContextProvider:
    """Produce a RAG context block for an agent turn.

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
        rag_config_id: uuid.UUID | None,
        query_text: str | None,
        agent_id: uuid.UUID | None = None,
    ) -> str | None:
        """Return a formatted RAG context string, or ``None`` if unavailable.

        Safe to call unconditionally — returns ``None`` when the config is
        missing, Qdrant is not configured, or retrieval fails for any reason.
        """
        if rag_config_id is None or self._qdrant_url is None or not query_text:
            return None
        try:
            from qdrant_client import AsyncQdrantClient

            from contexts.knowledge.application.ports import Reranker
            from contexts.knowledge.application.retrieve import RetrieveService
            from contexts.knowledge.infrastructure.embedders import router_embedder_for
            from contexts.knowledge.infrastructure.qdrant_store import QdrantStore
            from contexts.knowledge.infrastructure.repositories import RagConfigRepository

            cfg = await RagConfigRepository(self._db).get(rag_config_id)
            if cfg is None or cfg.embed_key_id is None:
                return None
            embedder = router_embedder_for(
                router=self._router,  # type: ignore[arg-type]
                key_id=cfg.embed_key_id,
                provider=cfg.embed_provider,
                model=cfg.embed_model,
            )
            reranker: Reranker | None = None
            if cfg.rerank_enabled and cfg.rerank_key_id is not None:
                try:
                    from contexts.knowledge.infrastructure.rerankers import RouterReranker

                    # Router-backed constructor (mirrors RouterEmbedder): the
                    # caller never touches key plaintext — the router unwraps
                    # the pinned rerank key on demand.  If the reranker module
                    # still has the legacy ``api_key`` constructor this raises
                    # TypeError and we retrieve without rerank.
                    reranker = RouterReranker(
                        router=self._router,  # type: ignore[arg-type]
                        key_id=cfg.rerank_key_id,
                        model=cfg.rerank_model or "rerank-3",
                    )
                except TypeError:
                    _log.warning(
                        "router-backed reranker unavailable for rag config %s; " "retrieving without rerank",
                        cfg.id,
                    )
            qclient = AsyncQdrantClient(
                url=self._qdrant_url,
                api_key=self._qdrant_api_key or None,
            )
            try:
                svc = RetrieveService(
                    self._db,
                    embedder=embedder,
                    qdrant=QdrantStore(qclient),
                    reranker=reranker,
                )
                chunks = await svc.query(
                    config_id=rag_config_id,
                    text=query_text,
                    agent_id=agent_id,
                    # rerank=None defers to cfg.rerank_enabled; without a
                    # reranker instance force it off so query() stays cheap.
                    rerank=None if reranker is not None else False,
                )
                if not chunks:
                    return None
                return str(svc.format_as_rag_message(chunks)["content"])
            finally:
                await qclient.close()
        except Exception:
            _log.warning(
                "RAG retrieval failed config=%s",
                rag_config_id,
                exc_info=True,
            )
            return None


__all__ = ["RagContextProvider"]
