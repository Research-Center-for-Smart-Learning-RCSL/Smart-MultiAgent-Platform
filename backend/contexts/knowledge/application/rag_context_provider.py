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

from contexts.knowledge.application.context_provider_text import normalise_queries
from shared_kernel import audit
from shared_kernel.tokens import estimate_tokens

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
        token_budget: int | None = None,
    ) -> RagContext | None:
        """Return the RAG context (prompt block + citable sources), or ``None``.

        Safe to call unconditionally — returns ``None`` when the config is
        missing, Qdrant is not configured, or retrieval fails for any reason.

        ``token_budget`` bounds the rendered block (F-16/R11.19): lowest-score
        chunks are dropped, and the last chunk that fits is truncated, so File
        RAG yields the space the narrow-scope precedence reserves for the graph
        blocks. ``sources`` still reflects the full retrieved set for citation.
        """
        queries = normalise_queries(query_text=query_text, query_texts=query_texts)
        if rag_config_id is None or self._qdrant_url is None or not queries:
            return None
        try:
            from qdrant_client import AsyncQdrantClient

            from contexts.keys.domain.errors import KeyProjectScopeError
            from contexts.keys.interfaces.facade import KeysFacade
            from contexts.knowledge.application.ports import Reranker
            from contexts.knowledge.application.retrieve import RetrievedChunk, RetrieveService
            from contexts.knowledge.infrastructure.embedders import router_embedder_for
            from contexts.knowledge.infrastructure.qdrant_store import QdrantStore
            from contexts.knowledge.infrastructure.repositories import RagConfigRepository

            cfg = await RagConfigRepository(self._db).get(rag_config_id)
            if cfg is None or cfg.embed_key_id is None:
                return None

            # F-1 (R7.04): a pinned embed/rerank key may issue a billed provider
            # call only while it is carried into the config's project. The router
            # is the authoritative gate — it raises KeyProjectScopeError before
            # any billed call. The knowledge layer only *reacts*, degrading so one
            # withdrawn key never bricks the turn. The embed key's scope is left
            # to the router and surfaces as KeyProjectScopeError from retrieval
            # below (embed failure → no RAG block anyway, so no re-run is wasted).
            # The rerank key is pre-checked here so its degrade is a clean
            # vector-only fallback without re-embedding. Each degradation is audited.
            keys_facade = KeysFacade(self._db)

            embedder = router_embedder_for(
                router=self._router,  # type: ignore[arg-type]
                key_id=cfg.embed_key_id,
                project_id=cfg.project_id,
                provider=cfg.embed_provider,
                model=cfg.embed_model,
            )
            reranker: Reranker | None = None
            if cfg.rerank_enabled and cfg.rerank_key_id is not None:
                if not await keys_facade.is_key_in_project_scope(cfg.rerank_key_id, cfg.project_id):
                    await self._emit_scope_degrade(cfg=cfg, key_id=cfg.rerank_key_id, capability="rerank")
                    # Vector-only: leave reranker=None.
                else:
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
                            project_id=cfg.project_id,
                            model=cfg.rerank_model or "rerank-3",
                        )
                    except TypeError:
                        _log.warning(
                            "router-backed reranker unavailable for rag config %s; "
                            "retrieving without rerank",
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
                try:
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
                except KeyProjectScopeError as err:
                    # The router refused a pinned key mid-retrieval (embed key
                    # withdrawn from the config's project — or, rarely, a rerank
                    # key withdrawn between its pre-check and use). No billed
                    # call happened; drop the RAG block for this turn and audit.
                    #
                    # Attribute to the failing capability by matching the embed key
                    # FIRST: embedding runs before rerank in retrieval, so when a
                    # config pins the SAME key for both, the call that raised is the
                    # embed. Keying on rerank_key_id first would mislabel that
                    # shared-key embed failure as "rerank".
                    capability = "embedding" if err.key_id == cfg.embed_key_id else "rerank"
                    await self._emit_scope_degrade(cfg=cfg, key_id=err.key_id, capability=capability)
                    return None
                effective_top_k = top_k or cfg.top_k or 8
                chunks = sorted(by_ref.values(), key=lambda c: c.score, reverse=True)[:effective_top_k]
                if not chunks:
                    return None
                sources = await self._build_sources(chunks)
                block = _format_rag_block(chunks, sources, token_budget=token_budget)
                if not block:
                    return None
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

    async def _emit_scope_degrade(
        self,
        *,
        cfg: Any,
        key_id: uuid.UUID,
        capability: str,
    ) -> None:
        """Record one audit event for a pinned-key carried-scope degradation.

        Metadata carries only identifiers (config/key/project ids) and the
        capability — never the key secret, which this layer never holds.

        Best-effort: a failure to write the audit row must not escalate the
        degradation (e.g. turn a rerank-only degrade into a full RAG drop), so
        the emit is guarded — the security guarantee (no billed out-of-scope
        call) is already upheld by the router regardless of this record.
        """
        _log.warning(
            "RAG pinned %s key not carried into project; degrading config=%s",
            capability,
            cfg.id,
        )
        try:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="rag.key_scope_degraded",
                    resource_type="rag_config",
                    resource_id=cfg.id,
                    metadata={
                        "project_id": str(cfg.project_id),
                        "key_id": str(key_id),
                        "capability": capability,
                        "reason": "key_not_carried",
                    },
                ),
            )
        except Exception:
            _log.warning(
                "failed to emit rag.key_scope_degraded audit for config=%s",
                cfg.id,
                exc_info=True,
            )

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


def _format_rag_block(
    chunks: list[Any], sources: list[dict[str, Any]], *, token_budget: int | None = None
) -> str:
    source_by_ref: dict[tuple[uuid.UUID, int], dict[str, Any]] = {}
    for source in sources:
        try:
            key = (uuid.UUID(str(source["document_id"])), int(source["chunk_idx"]))
        except (KeyError, TypeError, ValueError):
            continue
        source_by_ref[key] = source
    header = "Retrieved context:"
    body_lines: list[str] = [header]
    used = estimate_tokens(header)
    # Chunks arrive score-sorted (highest first). Under a budget, keep whole
    # chunks while they fit, then truncate the first chunk that overflows and
    # stop — lowest-score chunks are dropped, highest-score content preserved.
    for c in chunks:
        source = source_by_ref.get((c.document_id, c.chunk_idx), {})
        label = _source_label(source.get("filename"))
        if label:
            ref = f"source={label} doc={c.document_id} chunk={c.chunk_idx} score={c.score:.3f}"
        else:
            ref = f"doc={c.document_id} chunk={c.chunk_idx} score={c.score:.3f}"
        entry = f"[{ref}]\n{c.text}"
        if token_budget is None or used + estimate_tokens(entry) <= token_budget:
            body_lines.append(entry)
            used += estimate_tokens(entry)
            continue
        # Overflow: try to fit a truncated body for this chunk, then stop.
        prefix = f"[{ref}]\n"
        remaining = token_budget - used - estimate_tokens(prefix)
        truncated = _truncate_to_tokens(c.text, remaining) if remaining > 0 else ""
        if truncated:
            body_lines.append(f"{prefix}{truncated}...")
        break
    if len(body_lines) == 1:
        # Budget too small for even a truncated first chunk — omit the block.
        return ""
    block = "\n\n".join(body_lines)
    if token_budget is not None and estimate_tokens(block) > token_budget:
        # Per-entry accounting under-counts (Latin len//4 is summed per entry, not
        # once over the whole string); clamp the assembled block as a hard backstop
        # so the allocator's postcondition (estimate <= budget) always holds. The
        # clamp only trims the tail of the lowest-score surviving chunk's body (ref
        # lines sit at entry starts, never at the end), so reserve one token for an
        # explicit truncation marker rather than cutting the block silently.
        block = _truncate_to_tokens(block, max(0, token_budget - 1)).rstrip() + "..."
    return block


def _truncate_to_tokens(text: str, budget: int) -> str:
    """Longest prefix of ``text`` whose coarse token estimate is within ``budget``."""
    if budget <= 0:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def _source_label(value: object) -> str:
    label = " ".join(str(value or "").split())
    if len(label) <= _MAX_SOURCE_LABEL_CHARS:
        return label
    return label[: _MAX_SOURCE_LABEL_CHARS - 3].rstrip() + "..."


__all__ = ["RagContext", "RagContextProvider"]
