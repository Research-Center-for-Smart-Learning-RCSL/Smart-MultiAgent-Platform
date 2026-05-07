"""Tavily :class:`SearchAdapter` — v1 end-to-end provider (R12.17).

Routes outbound HTTP through the supplied :class:`EgressProxyClient` so the
call is subject to the project's allowlist and RFC1918/metadata blocks. The
Tavily ``api_key`` is passed in the JSON body per their API.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from contexts.agents.application.mcp_ports import EgressProxyClient
from contexts.agents.domain.mcp import SearchResult

_TAVILY_ENDPOINT = "https://api.tavily.com/search"


def _parse_published(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class TavilyAdapter:
    name: str = "tavily"

    async def search(
        self,
        query: str,
        *,
        top_k: int,
        locale: str,
        freshness: Literal["any", "day", "week", "month", "year"],
        api_key: bytes,
        proxy: EgressProxyClient,
        project_id: uuid.UUID,
    ) -> list[SearchResult]:
        body: dict[str, Any] = {
            "api_key": api_key.decode("utf-8").strip(),
            "query": query,
            "max_results": int(top_k),
            "search_depth": "basic",
        }
        # Tavily's "topic"/"days" encode freshness — map our enum to days.
        days_map: dict[str, int | None] = {
            "any": None,
            "day": 1,
            "week": 7,
            "month": 30,
            "year": 365,
        }
        days = days_map.get(freshness)
        if days is not None:
            body["days"] = days
        if locale and locale.startswith("en"):
            # Tavily defaults to English; only override when caller is explicit.
            body["include_answer"] = False

        status, _headers, payload = await proxy.request(
            method="POST",
            url=_TAVILY_ENDPOINT,
            project_id=project_id,
            headers={"content-type": "application/json"},
            json_body=body,
        )
        if status >= 400:
            raise RuntimeError(
                f"tavily returned HTTP {status}: {payload[:256]!r}",
            )
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"tavily returned non-JSON: {exc}") from exc

        out: list[SearchResult] = []
        for idx, item in enumerate(parsed.get("results", []) or []):
            if not isinstance(item, dict):
                continue
            out.append(
                SearchResult(
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                    snippet=str(item.get("content", "") or item.get("snippet", "")),
                    published_at=_parse_published(item.get("published_date")),
                    score=float(item.get("score", max(0.0, 1.0 - idx * 0.05))),
                )
            )
        return out


__all__ = ["TavilyAdapter"]
