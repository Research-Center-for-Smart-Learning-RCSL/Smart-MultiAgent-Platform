"""Brave Search :class:`SearchAdapter` (R12.17).

Routes through :class:`EgressProxyClient` — the Brave API key is sent
via the ``X-Subscription-Token`` header.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from contexts.agents.application.mcp_ports import EgressProxyClient
from contexts.agents.domain.mcp import SearchResult

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

_FRESHNESS_MAP: dict[str, str | None] = {
    "any": None,
    "day": "pd",
    "week": "pw",
    "month": "pm",
    "year": "py",
}


def _parse_age(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class BraveAdapter:
    name: str = "brave"

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
        config: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        params: dict[str, Any] = {
            "q": query,
            "count": int(top_k),
        }
        fresh = _FRESHNESS_MAP.get(freshness)
        if fresh is not None:
            params["freshness"] = fresh

        status, _headers, payload = await proxy.request(
            method="GET",
            url=_BRAVE_ENDPOINT,
            project_id=project_id,
            headers={
                "X-Subscription-Token": api_key.decode("utf-8").strip(),
                "Accept": "application/json",
            },
            params=params,
        )
        if status >= 400:
            raise RuntimeError(
                f"brave returned HTTP {status}: {payload[:256]!r}",
            )
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"brave returned non-JSON: {exc}") from exc

        out: list[SearchResult] = []
        for idx, item in enumerate(
            parsed.get("web", {}).get("results", []) or []
        ):
            if not isinstance(item, dict):
                continue
            out.append(
                SearchResult(
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                    snippet=str(item.get("description", "")),
                    published_at=_parse_age(item.get("page_age")),
                    score=float(max(0.0, 1.0 - idx * 0.05)),
                )
            )
        return out


__all__ = ["BraveAdapter"]
