"""Concrete provider adapters + router factory (K.1).

Closes the seam the router shipped with — ``provider_router.py`` declared the
``ProviderAdapter`` protocol and a ``dict[ApiKeyProvider, ProviderAdapter]``
constructor arg but deferred the implementations to "Phase E", which never
delivered. This package is that delivery and ``build_router`` is the router's
first production construction site.

One adapter per provider (the router maps ``key.provider`` → one adapter), each
serving every capability that provider offers per the R7.01 table:

    claude  → LLM_CHAT (stream)
    openai  → LLM_CHAT (stream) + EMBEDDING
    gemini  → LLM_CHAT (stream) + EMBEDDING
    voyage  → EMBEDDING
    cohere  → RERANK
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.application.provider_router import (
    ProviderAdapter,
    ProviderRouter,
    RouterConfig,
)
from contexts.keys.domain.providers import ApiKeyProvider
from contexts.keys.infrastructure.adapters.anthropic import AnthropicAdapter
from contexts.keys.infrastructure.adapters.cohere import CohereAdapter
from contexts.keys.infrastructure.adapters.gemini import GeminiAdapter
from contexts.keys.infrastructure.adapters.openai import OpenAIAdapter
from contexts.keys.infrastructure.adapters.voyage import VoyageAdapter


def build_adapters() -> dict[ApiKeyProvider, ProviderAdapter]:
    """Instantiate one adapter per provider. Adapters are stateless + cheap."""
    return {
        ApiKeyProvider.CLAUDE: AnthropicAdapter(),
        ApiKeyProvider.OPENAI: OpenAIAdapter(),
        ApiKeyProvider.GEMINI: GeminiAdapter(),
        ApiKeyProvider.VOYAGE: VoyageAdapter(),
        ApiKeyProvider.COHERE: CohereAdapter(),
    }


def build_router(db: AsyncSession, *, config: RouterConfig | None = None) -> ProviderRouter:
    """Construct a fully-wired :class:`ProviderRouter` for ``db``.

    The single entry point every runtime caller (K.2 turn engine, graphrag
    builder, RAG ingest/retrieve) uses to obtain a router — so no caller
    re-implements the adapter map or bypasses rotation/quota/usage accounting.
    """
    return ProviderRouter(db, build_adapters(), config=config)


__all__ = [
    "AnthropicAdapter",
    "CohereAdapter",
    "GeminiAdapter",
    "OpenAIAdapter",
    "VoyageAdapter",
    "build_adapters",
    "build_router",
]
