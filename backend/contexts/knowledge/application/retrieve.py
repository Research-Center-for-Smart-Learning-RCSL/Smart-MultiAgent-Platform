"""RAG retrieval + optional rerank (R10.07 – R10.09, R10.11).

Public surface:

    retrieve = RetrieveService(db, embedder=…, qdrant=…, reranker=…)
    chunks = await retrieve.query(
        config_id=…, text="What are the retention rules?",
        agent_id=…, top_k=None, rerank=None,
    )

Returns a list of :class:`RetrievedChunk` sorted by descending relevance.
The *injection* of the result as a `{"type":"rag"}` system message is the
caller's responsibility (conversation context, F) — this service is
oblivious to chat plumbing so it remains trivially testable.

SoC:
- Only this module talks to Qdrant + rerankers.
- Permission filtering (R10.11 — scope to caller's accessible projects)
  runs at the **caller's** edge because that's where the principal lives.
  Within this service we trust the ``config_id`` has already been looked
  up through the correct project context — the repository's
  `get(config_id)` returns only live rows, and the config row carries
  `project_id`, so Qdrant's collection name and the payload filters never
  cross projects.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.ports import Embedder, Reranker
from contexts.knowledge.domain.errors import RagConfigNotFound
from contexts.knowledge.domain.models import RagConfig
from contexts.knowledge.infrastructure.qdrant_store import QdrantStore
from contexts.knowledge.infrastructure.repositories import (
    RagChunkRepository,
    RagConfigRepository,
    RagDocumentRepository,
)

_log = logging.getLogger(__name__)

__all__ = ["RetrievedChunk", "RetrieveService"]


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    document_id: uuid.UUID
    chunk_idx: int
    text: str
    score: float


class RetrieveService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        embedder: Embedder,
        qdrant: QdrantStore,
        reranker: Reranker | None = None,
    ) -> None:
        self._db = db
        self._embedder = embedder
        self._qdrant = qdrant
        self._reranker = reranker
        self._configs = RagConfigRepository(db)
        self._chunks = RagChunkRepository(db)
        self._docs = RagDocumentRepository(db)

    async def _load_config(self, config_id: uuid.UUID) -> RagConfig:
        cfg = await self._configs.get(config_id)
        if cfg is None:
            raise RagConfigNotFound(str(config_id))
        return cfg

    async def query(
        self,
        *,
        config_id: uuid.UUID,
        text: str,
        agent_id: uuid.UUID | None = None,
        top_k: int | None = None,
        rerank: bool | None = None,
        allow_unrestricted: bool = False,
    ) -> list[RetrievedChunk]:
        # R10.11: the per-document agent allowlist is only applied when an
        # agent_id is given. Omitting it silently widens retrieval to every
        # retrievable document in the config, so require an explicit opt-in
        # (`allow_unrestricted`, for admin/test entry points) rather than letting
        # a caller that forgets agent_id quietly bypass the allowlist.
        if agent_id is None and not allow_unrestricted:
            raise ValueError("retrieval requires agent_id (or allow_unrestricted=True for admin use)")
        cfg = await self._load_config(config_id)
        effective_top_k = top_k or cfg.top_k or 8
        do_rerank = cfg.rerank_enabled if rerank is None else rerank

        # Scope the Qdrant search to this config's documents. The collection is
        # per-PROJECT (rag_{project_id}) and shared across configs, so the doc_id
        # filter is what limits results to THIS config — without it a search
        # would leak other configs' chunks. When an agent_id is given we further
        # narrow to that agent's allowlist (R10.11); the no-agent path (admin /
        # tests) is still config-scoped, never project-wide.
        #
        # doc_ids size scales with the config's (agent-visible) document count —
        # fine at this scale. If a config ever holds very many documents, prefer
        # a per-config collection or a config_id payload filter over an unbounded
        # doc_id list rather than dropping the filter (which would un-scope).
        if agent_id is not None:
            doc_ids = await self._docs.allowed_document_ids(config_id=config_id, agent_id=agent_id)
        else:
            doc_ids = await self._docs.retrievable_document_ids(config_id=config_id)
        if not doc_ids:
            return []

        vecs = await self._embedder.embed_batch([text])
        if not vecs:
            from contexts.knowledge.infrastructure.embedders import EmbeddingError

            raise EmbeddingError(0, "embedder returned no vectors for query")
        query_vec = vecs[0]

        # Over-fetch only when reranking, to give the reranker headroom (R10.08).
        fetch_k = effective_top_k * 4 if do_rerank else effective_top_k
        hits = await self._qdrant.search(
            project_id=cfg.project_id,
            query_vector=query_vec,
            top_k=fetch_k,
            doc_ids=doc_ids,
        )
        if not hits:
            return []

        # Hydrate chunk text from Postgres (scan-status gate applied there).
        rows = await self._chunks.lookup_points([h.point_id for h in hits])
        by_pt: dict[uuid.UUID, tuple[int, uuid.UUID, str]] = {
            r.qdrant_point_id: (r.chunk_idx, r.document_id, r.text) for r in rows
        }
        candidates: list[RetrievedChunk] = []
        for h in hits:
            info = by_pt.get(h.point_id)
            if info is None:
                continue
            chunk_idx, doc_id, chunk_text = info
            candidates.append(
                RetrievedChunk(
                    document_id=doc_id,
                    chunk_idx=chunk_idx,
                    text=chunk_text,
                    score=h.score,
                )
            )

        if do_rerank and self._reranker is not None and candidates:
            rr = await self._reranker.rerank(
                query=text,
                candidates=[c.text for c in candidates],
                top_k=effective_top_k,
            )
            reranked: list[RetrievedChunk] = []
            for r in rr:
                if 0 <= r.index < len(candidates):
                    reranked.append(
                        RetrievedChunk(
                            document_id=candidates[r.index].document_id,
                            chunk_idx=candidates[r.index].chunk_idx,
                            text=candidates[r.index].text,
                            score=r.score,
                        )
                    )
                else:
                    _log.warning(
                        "reranker returned out-of-bounds index %d (candidates=%d) — dropped",
                        r.index, len(candidates),
                    )
            # Do not trust the provider's ordering — sort by descending score so
            # the injected context is genuinely ranked even if a reranker ever
            # returns results in input order.
            reranked.sort(key=lambda c: c.score, reverse=True)
            return reranked[:effective_top_k]

        return candidates[:effective_top_k]

    def format_as_rag_message(self, chunks: list[RetrievedChunk]) -> dict[str, Any]:
        """Build the {"type":"rag"} system message payload (R10.09).

        Callers attach this verbatim to `messages.metadata` right before
        the user turn — it is not persisted as chat UI.
        """
        body_lines: list[str] = ["Retrieved context:"]
        for c in chunks:
            body_lines.append(f"[doc={c.document_id} chunk={c.chunk_idx} score={c.score:.3f}]\n{c.text}")
        return {
            "role": "system",
            "content": "\n\n".join(body_lines),
            "metadata": {
                "type": "rag",
                "chunk_refs": [{"document_id": str(c.document_id), "chunk_idx": c.chunk_idx} for c in chunks],
            },
        }
