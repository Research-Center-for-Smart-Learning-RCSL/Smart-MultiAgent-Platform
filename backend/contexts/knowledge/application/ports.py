"""Protocols for I/O surfaces the knowledge context depends on.

Keeping these Protocols in the application layer rather than pulling
concrete clients (OpenAI, Cohere, MinIO) lets us:

1. Swap implementations per deployment (BYO embedding provider).
2. Test the ingest + retrieve paths with trivial fakes.
3. Keep the domain layer framework-free.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "BlobStore",
    "Embedder",
    "Reranker",
    "RerankResult",
]


class BlobStore(Protocol):
    """MinIO-like object surface for RAG source files."""

    async def put(
        self,
        *,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
    ) -> str:
        """Write ``data`` and return the stored path (`bucket/key` joined)."""

    async def get(self, *, bucket: str, key: str) -> bytes:
        """Read an object. Raises if missing."""


class Embedder(Protocol):
    """Provider-agnostic embedding surface (R10.05).

    Implementations pick the API key based on the RAG config's
    ``embed_key_id`` field — the ingest service passes both the key id and
    the `(provider, model)` pair through.
    """

    vector_size: int

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input. Length == len(texts)."""


@dataclass(frozen=True, slots=True)
class RerankResult:
    index: int  # index into the original candidate list
    score: float


class Reranker(Protocol):
    """Cohere `rerank-3` or local `bge-reranker-v2-m3` (R10.08)."""

    async def rerank(
        self,
        *,
        query: str,
        candidates: list[str],
        top_k: int,
    ) -> list[RerankResult]:
        """Return up to ``top_k`` candidates sorted by descending score."""
