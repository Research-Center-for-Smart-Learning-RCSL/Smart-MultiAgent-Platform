"""``web_search`` built-in tool orchestrator (E.11 / §12.4 / R12.09–R12.17).

Flow:

1. Find the active search key for the project. If none, raise
   :class:`SearchKeyNotConfigured` (R12.10).
2. Resolve the adapter by provider (R12.17 — Tavily ships in v1).
3. Check the Redis cache keyed by ``hash(provider,query_norm,top_k,locale,freshness)``
   with TTL = 10 minutes (R12.13). A cache hit returns immediately and
   consumes no rate-limit quota — quota gates real provider egress only.
4. Miss → check the project-scoped rate limit (R12.14 — default 60/min,
   tunable), then call the adapter via the Egress Proxy.
5. Cap the serialised result at 4 KB (R12.12).
6. Audit ``mcp.tool_invoked`` with the query truncated to 256 chars (R12.15).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.mcp_ports import (
    EgressProxyClient,
    SearchAdapter,
    SearchCache,
    SearchRateLimiter,
)
from contexts.agents.domain.errors import (
    SearchKeyNotConfigured,
    SearchQuotaExceeded,
)
from contexts.agents.domain.mcp import SearchResult
from contexts.keys.domain.search import SearchKey, SearchProvider
from contexts.keys.infrastructure.search_repository import SearchKeyRepository
from shared_kernel import audit

_MAX_RESULTS = 20
_DEFAULT_TOP_K = 5
_MAX_SERIALISED_BYTES = 4096
_CACHE_TTL_S = 600
_DEFAULT_QUERY_TRUNC = 256


def _cache_key(provider: str, query: str, top_k: int, locale: str, freshness: str) -> str:
    payload = f"{provider}|{query.strip().lower()}|{top_k}|{locale}|{freshness}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"search:{digest}"


def _cap_results(results: list[SearchResult]) -> list[SearchResult]:
    """Trim ``results`` so the serialised payload fits in 4 KB (R12.12)."""
    kept: list[SearchResult] = []
    running = 2  # ``[]`` skeleton
    for r in results:
        blob = json.dumps(
            {
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "published_at": r.published_at.isoformat() if r.published_at else None,
                "score": r.score,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        # +1 for the separating comma between items (not before the first).
        sep = 1 if kept else 0
        if running + len(blob) + sep > _MAX_SERIALISED_BYTES:
            break
        running += len(blob) + sep
        kept.append(r)
    return kept


@dataclass
class WebSearchTool:
    agent_id: uuid.UUID
    project_id: uuid.UUID
    db: AsyncSession
    adapters: dict[SearchProvider, SearchAdapter]
    cache: SearchCache
    rate_limiter: SearchRateLimiter
    proxy: EgressProxyClient
    rate_limit_per_minute: int = 60
    _audit_query_trunc: int = field(default=_DEFAULT_QUERY_TRUNC)

    async def _active_key(self) -> SearchKey:
        repo = SearchKeyRepository(self.db)
        keys = await repo.list_for_project(self.project_id)
        active = [k for k in keys if k.is_active]
        if not active:
            raise SearchKeyNotConfigured(
                f"project {self.project_id} has no active search key",
            )
        # There can be only one active key per project (partial-unique index).
        return active[0]

    async def search(
        self,
        query: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
        locale: str = "en-US",
        freshness: Literal["any", "day", "week", "month", "year"] = "any",
    ) -> list[SearchResult]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        top_k = max(1, min(int(top_k), _MAX_RESULTS))

        # Step 1 — active key.
        key = await self._active_key()
        adapter = self.adapters.get(key.provider)
        if adapter is None:
            raise SearchKeyNotConfigured(
                f"no adapter registered for provider {key.provider.value}",
            )

        # Step 3 — cache. DOM-12: checked BEFORE the rate limiter so a cache
        # hit costs neither a provider call nor a quota token; the rate limit
        # exists to throttle real egress, and a cached answer makes none.
        ck = _cache_key(key.provider.value, query, top_k, locale, freshness)
        cached = await self.cache.get(ck)
        if cached is not None:
            capped = _cap_results(cached)
            await self._audit(query, key.provider, "cache", len(capped))
            return capped

        # Step 4 — cache miss: consume a rate-limit token before egress (R12.14).
        allowed = await self.rate_limiter.try_acquire(
            project_id=self.project_id,
            limit_per_minute=self.rate_limit_per_minute,
        )
        if not allowed:
            raise SearchQuotaExceeded(
                f"project {self.project_id} exceeded {self.rate_limit_per_minute}/min",
            )

        # Step 5 — unwrap the search key and call the adapter.
        plaintext = await self._unwrap_search_key(key.id)
        try:
            results = await adapter.search(
                query,
                top_k=top_k,
                locale=locale,
                freshness=freshness,
                api_key=plaintext,
                proxy=self.proxy,
                project_id=self.project_id,
                config=key.config,
            )
        finally:
            # Best-effort zeroisation — bytes are immutable so swap reference.
            del plaintext

        capped = _cap_results(results)
        await self.cache.set(ck, capped, ttl_s=_CACHE_TTL_S)
        await self._audit(query, key.provider, "live", len(capped))
        return capped

    async def _unwrap_search_key(self, key_id: uuid.UUID) -> bytes:
        """Fallback unwrap path — mirrors ``KeysFacade.unwrap_api_key_plaintext``
        but for the ``search_keys`` table's AAD namespace.
        """
        from contexts.keys.infrastructure.search_repository import (
            SearchKeyRepository,
        )
        from shared_kernel.security import envelope as env

        repo = SearchKeyRepository(self.db)
        loaded = await repo.get_active_with_envelope(key_id)
        if loaded is None:
            raise SearchKeyNotConfigured(str(key_id))
        _sk, record = loaded
        return env.decrypt_envelope(record, env.search_key_aad(key_id))

    async def _audit(
        self,
        query: str,
        provider: SearchProvider,
        source: Literal["cache", "live"],
        result_count: int,
    ) -> None:
        trimmed = query[: self._audit_query_trunc]
        await audit.emit(
            self.db,
            audit.AuditEvent(
                action="mcp.tool_invoked",
                resource_type="agent",
                resource_id=self.agent_id,
                metadata={
                    "tool": "web_search",
                    "provider": provider.value,
                    "query": trimmed,
                    "source": source,
                    "result_count": result_count,
                    "project_id": str(self.project_id),
                },
            ),
        )


__all__ = ["WebSearchTool"]
