"""Reranker adapters (R10.08).

Two implementations exposed; both satisfy :class:`Reranker`:

- :class:`RouterReranker` — BYO rerank key (e.g. Cohere ``rerank-3``) signed
  through :meth:`ProviderRouter.call_single_key` with the RAG config's pinned
  ``rerank_key_id`` (capability = `rerank`). Mirrors ``RouterEmbedder``: the
  caller never touches key material, the call goes through the concrete
  adapter, and a `key_usage_events` row is recorded per call (R7.12).
- :class:`LocalBgeReranker` — calls an internal ``bge-reranker-v2-m3``
  HTTP service. No API key required.

Both return results sorted by descending score.
"""

from __future__ import annotations

import uuid

import httpx

from contexts.keys.application.provider_router import ProviderRequest, ProviderRouter
from contexts.keys.domain.providers import ProviderCapability
from contexts.knowledge.application.ports import Reranker, RerankResult

__all__ = ["LocalBgeReranker", "RerankError", "RouterReranker"]


class RerankError(RuntimeError):
    """Provider returned a non-2xx for a rerank call (scrubbed)."""

    def __init__(self, http_status: int, detail: object = None) -> None:
        super().__init__(f"rerank provider failed (HTTP {http_status})")
        self.http_status = http_status
        self.detail = detail


class RouterReranker(Reranker):
    """Concrete :class:`Reranker` signing through one pinned key via the router."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        key_id: uuid.UUID,
        model: str,
    ) -> None:
        self._router = router
        self._key_id = key_id
        self._model = model

    async def rerank(
        self,
        *,
        query: str,
        candidates: list[str],
        top_k: int,
    ) -> list[RerankResult]:
        if not candidates:
            return []
        result = await self._router.call_single_key(
            key_id=self._key_id,
            request=ProviderRequest(
                capability=ProviderCapability.RERANK,
                payload={
                    "model": self._model,
                    "query": query,
                    "documents": candidates,
                    "top_n": top_k,
                },
            ),
        )
        if result.http_status != 200:
            raise RerankError(result.http_status, result.body.get("error"))
        return [
            RerankResult(index=int(e["index"]), score=float(e["relevance_score"]))
            for e in result.body.get("results") or []
        ]


class LocalBgeReranker(Reranker):
    def __init__(
        self,
        *,
        base_url: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        # base_url points at the internal `bge-reranker-v2-m3` service.
        self._base = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=15.0)

    async def rerank(
        self,
        *,
        query: str,
        candidates: list[str],
        top_k: int,
    ) -> list[RerankResult]:
        if not candidates:
            return []
        r = await self._http.post(
            f"{self._base}/rerank",
            json={"query": query, "candidates": candidates, "top_k": top_k},
        )
        r.raise_for_status()
        payload = r.json().get("results", [])
        return [RerankResult(index=int(e["index"]), score=float(e["score"])) for e in payload]

    async def close(self) -> None:
        """Close the underlying httpx client to release connections."""
        await self._http.aclose()
