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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger(__name__)
_MAX_SOURCE_LABEL_CHARS = 160


@dataclass(frozen=True, slots=True)
class RagContext:
    """Result of a RAG retrieval for one turn.

    ``block`` is folded into the system prompt; ``sources`` is persisted on the
    agent reply's ``metadata.rag_sources`` so the UI can cite what was retrieved
    (one entry per retrieved chunk, filename resolved for display).
    """

    block: str
    sources: list[dict[str, Any]]


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
        query_text: str | None = None,
        query_texts: Sequence[str] | None = None,
        agent_id: uuid.UUID | None = None,
        top_k: int | None = None,
    ) -> RagContext | None:
        """Return the RAG context (prompt block + citable sources), or ``None``.

        Safe to call unconditionally — returns ``None`` when the config is
        missing, Qdrant is not configured, or retrieval fails for any reason.
        """
        queries = _normalise_queries(query_text=query_text, query_texts=query_texts)
        if rag_config_id is None or self._qdrant_url is None or not queries:
            return None
        try:
            from qdrant_client import AsyncQdrantClient

            from contexts.knowledge.application.ports import Reranker
            from contexts.knowledge.application.retrieve import RetrievedChunk, RetrieveService
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
                by_ref: dict[tuple[uuid.UUID, int], RetrievedChunk] = {}
                for query in queries:
                    for chunk in await svc.query(
                        config_id=rag_config_id,
                        text=query,
                        agent_id=agent_id,
                        top_k=top_k,
                        # rerank=None defers to cfg.rerank_enabled; without a
                        # reranker instance force it off so query() stays cheap.
                        rerank=None if reranker is not None else False,
                    ):
                        ref = (chunk.document_id, chunk.chunk_idx)
                        previous = by_ref.get(ref)
                        if previous is None or chunk.score > previous.score:
                            by_ref[ref] = chunk
                effective_top_k = top_k or cfg.top_k or 8
                chunks = sorted(by_ref.values(), key=lambda c: c.score, reverse=True)[:effective_top_k]
                if not chunks:
                    return None
                sources = await self._build_sources(chunks)
                block = _format_rag_block(chunks, sources)
                return RagContext(block=block, sources=sources)
            finally:
                await qclient.close()
        except Exception:
            _log.warning(
                "RAG retrieval failed config=%s",
                rag_config_id,
                exc_info=True,
            )
            return None

    async def _build_sources(self, chunks: list[Any]) -> list[dict[str, Any]]:
        """Shape retrieved chunks into citable sources for the reply metadata.

        One entry per chunk (a document can contribute several), preserving the
        retriever's ranking. Filename is best-effort — a since-deleted document
        falls back to ``None`` rather than blocking the citation.
        """
        from contexts.knowledge.infrastructure.repositories import RagDocumentRepository

        distinct_ids = {c.document_id for c in chunks}
        docs = await RagDocumentRepository(self._db).get_many(list(distinct_ids))
        filenames: dict[uuid.UUID, str] = {d.id: d.filename for d in docs}
        return [
            {
                "document_id": str(c.document_id),
                "filename": filenames.get(c.document_id),
                "chunk_idx": c.chunk_idx,
                "score": round(c.score, 4),
            }
            for c in chunks
        ]


def _normalise_queries(*, query_text: str | None, query_texts: Sequence[str] | None) -> list[str]:
    queries: list[str] = []
    for raw in ([query_text] if query_text is not None else []) + list(query_texts or []):
        text = " ".join(str(raw or "").split())
        if text and text not in queries:
            queries.append(text)
    return queries


def _format_rag_block(chunks: list[Any], sources: list[dict[str, Any]]) -> str:
    source_by_ref: dict[tuple[uuid.UUID, int], dict[str, Any]] = {}
    for source in sources:
        try:
            key = (uuid.UUID(str(source["document_id"])), int(source["chunk_idx"]))
        except (KeyError, TypeError, ValueError):
            continue
        source_by_ref[key] = source
    body_lines: list[str] = ["Retrieved context:"]
    for c in chunks:
        source = source_by_ref.get((c.document_id, c.chunk_idx), {})
        label = _source_label(source.get("filename"))
        if label:
            ref = f"source={label} doc={c.document_id} chunk={c.chunk_idx} score={c.score:.3f}"
        else:
            ref = f"doc={c.document_id} chunk={c.chunk_idx} score={c.score:.3f}"
        body_lines.append(f"[{ref}]\n{c.text}")
    return "\n\n".join(body_lines)


def _source_label(value: object) -> str:
    label = " ".join(str(value or "").split())
    if len(label) <= _MAX_SOURCE_LABEL_CHARS:
        return label
    return label[: _MAX_SOURCE_LABEL_CHARS - 3].rstrip() + "..."


__all__ = ["RagContext", "RagContextProvider"]
