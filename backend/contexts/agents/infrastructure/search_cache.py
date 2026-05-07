"""Redis-backed :class:`SearchCache` (R12.13)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from contexts.agents.domain.mcp import SearchResult


def _encode(results: list[SearchResult]) -> str:
    return json.dumps(
        [
            {
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "published_at": r.published_at.isoformat() if r.published_at else None,
                "score": r.score,
            }
            for r in results
        ],
        separators=(",", ":"),
    )


def _decode(raw: str) -> list[SearchResult]:
    parsed = json.loads(raw)
    out: list[SearchResult] = []
    for entry in parsed:
        pub_raw = entry.get("published_at")
        pub: datetime | None = None
        if pub_raw:
            try:
                pub = datetime.fromisoformat(pub_raw)
            except ValueError:
                pub = None
        out.append(
            SearchResult(
                title=str(entry.get("title", "")),
                url=str(entry.get("url", "")),
                snippet=str(entry.get("snippet", "")),
                published_at=pub,
                score=float(entry.get("score", 0.0)),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class RedisSearchCache:
    """Bound to the shared Redis client from :mod:`shared_kernel.auth.clients`.

    Redis import stays lazy — callers exercise the cache in integration; the
    unit tests swap in an in-memory fake.
    """

    async def get(self, cache_key: str) -> list[SearchResult] | None:
        from shared_kernel.auth.clients import get_redis

        raw: Any = await get_redis().get(cache_key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return _decode(raw)
        except (ValueError, TypeError):
            return None

    async def set(self, cache_key: str, results: list[SearchResult], *, ttl_s: int) -> None:
        from shared_kernel.auth.clients import get_redis

        await get_redis().set(cache_key, _encode(results), ex=max(1, int(ttl_s)))


__all__ = ["RedisSearchCache"]
