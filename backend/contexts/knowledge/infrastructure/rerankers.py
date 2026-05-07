"""Reranker adapters (R10.08).

Two implementations exposed; both satisfy :class:`Reranker`:

- :class:`CohereReranker` — ``rerank-3`` via the Cohere API. Uses the BYO
  ``rerank_key_id`` (capability = `rerank`).
- :class:`LocalBgeReranker` — calls an internal ``bge-reranker-v2-m3``
  HTTP service. No API key required.

Both return results sorted by descending score.
"""

from __future__ import annotations

import httpx

from contexts.knowledge.application.ports import Reranker, RerankResult

__all__ = ["CohereReranker", "LocalBgeReranker"]


class CohereReranker(Reranker):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "rerank-3",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
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
            "https://api.cohere.com/v2/rerank",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "query": query,
                "documents": candidates,
                "top_n": top_k,
            },
        )
        r.raise_for_status()
        payload = r.json().get("results", [])
        return [RerankResult(index=int(e["index"]), score=float(e["relevance_score"])) for e in payload]


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
