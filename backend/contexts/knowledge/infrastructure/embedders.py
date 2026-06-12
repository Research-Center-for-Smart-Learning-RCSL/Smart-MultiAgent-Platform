"""Router-backed embedder for the R10.05 whitelist (K.1).

Embedding calls flow through :meth:`ProviderRouter.call_single_key` — the
*pinned-key* path (no rotation: a RAG config pins one ``embed_key_id`` and a
collection's vector dimensions must stay stable). This kills the previous
``KeysFacade.unwrap_api_key_plaintext`` + raw-httpx pattern (the caller no
longer touches key material) and records a `key_usage_events` row per batch
(R7.12).

The vector sizes below mirror current provider dimensions as of 2026-Q1 and
are authoritative for Qdrant ``ensure_collection`` sizing. Changing provider
defaults means migrating collections — tracked in
``docs/implement/E-agents-knowledge.md`` Risks.
"""

from __future__ import annotations

import uuid

from contexts.keys.application.provider_router import ProviderRequest, ProviderRouter
from contexts.keys.domain.providers import ProviderCapability
from contexts.knowledge.application.ports import Embedder

__all__ = ["EmbeddingError", "RouterEmbedder", "router_embedder_for"]


_VECTOR_SIZES: dict[tuple[str, str], int] = {
    ("openai", "text-embedding-3-small"): 1536,
    ("openai", "text-embedding-3-large"): 3072,
    ("gemini", "text-embedding-004"): 768,
    ("voyage", "voyage-3"): 1024,
}


class EmbeddingError(RuntimeError):
    """Provider returned a non-2xx for an embedding batch (scrubbed)."""

    def __init__(self, http_status: int, detail: object = None) -> None:
        super().__init__(f"embedding provider failed (HTTP {http_status})")
        self.http_status = http_status
        self.detail = detail


def _vector_size(provider: str, model: str) -> int:
    try:
        return _VECTOR_SIZES[(provider, model)]
    except KeyError as exc:
        raise ValueError(f"unknown embedder ({provider}, {model})") from exc


class RouterEmbedder(Embedder):
    """Concrete :class:`Embedder` signing through one pinned key via the router."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        key_id: uuid.UUID,
        provider: str,
        model: str,
    ) -> None:
        self._router = router
        self._key_id = key_id
        self._model = model
        self.vector_size = _vector_size(provider, model)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        result = await self._router.call_single_key(
            key_id=self._key_id,
            request=ProviderRequest(
                capability=ProviderCapability.EMBEDDING,
                payload={"model": self._model, "input": texts},
            ),
        )
        if result.http_status != 200:
            raise EmbeddingError(result.http_status, result.body.get("error"))
        return list(result.body.get("embeddings") or [])


def router_embedder_for(
    *,
    router: ProviderRouter,
    key_id: uuid.UUID,
    provider: str,
    model: str,
) -> Embedder:
    """Build a pinned-key embedder; validates the (provider, model) dimension."""
    return RouterEmbedder(router=router, key_id=key_id, provider=provider, model=model)
