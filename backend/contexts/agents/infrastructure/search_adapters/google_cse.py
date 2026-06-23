"""Google Custom Search Engine :class:`SearchAdapter` (R12.17).

Routes through :class:`EgressProxyClient`. The API key is sent as the
``key`` query parameter; the ``cx`` (search engine ID) is stored in the
search-key's ``config`` column and forwarded as the ``cx`` parameter.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from contexts.agents.application.mcp_ports import EgressProxyClient
from contexts.agents.domain.mcp import SearchResult

_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"

_FRESHNESS_MAP: dict[str, str | None] = {
    "any": None,
    "day": "d[1]",
    "week": "w[1]",
    "month": "m[1]",
    "year": "y[1]",
}


def _parse_snippet_date(snippet: str) -> datetime | None:
    """Best-effort extraction of the leading date Google CSE prepends."""
    if not snippet:
        return None
    parts = snippet.split(" ... ", 1)
    if len(parts) < 2:
        return None
    try:
        return datetime.fromisoformat(parts[0].strip().replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class GoogleCseAdapter:
    name: str = "google_cse"

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
        cx = (config or {}).get("cx", "")
        if not cx:
            raise RuntimeError("google_cse adapter requires a cx (search engine ID) in the search key config")

        params: dict[str, Any] = {
            "key": api_key.decode("utf-8").strip(),
            "cx": cx,
            "q": query,
            "num": min(int(top_k), 10),
        }
        date_restrict = _FRESHNESS_MAP.get(freshness)
        if date_restrict is not None:
            params["dateRestrict"] = date_restrict

        status, _headers, payload = await proxy.request(
            method="GET",
            url=_CSE_ENDPOINT,
            project_id=project_id,
            params=params,
        )
        if status >= 400:
            raise RuntimeError(
                f"google_cse returned HTTP {status}: {payload[:256]!r}",
            )
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"google_cse returned non-JSON: {exc}") from exc

        out: list[SearchResult] = []
        for idx, item in enumerate(parsed.get("items", []) or []):
            if not isinstance(item, dict):
                continue
            snippet = str(item.get("snippet", ""))
            out.append(
                SearchResult(
                    title=str(item.get("title", "")),
                    url=str(item.get("link", "")),
                    snippet=snippet,
                    published_at=_parse_snippet_date(snippet),
                    score=float(max(0.0, 1.0 - idx * 0.05)),
                )
            )
        return out


__all__ = ["GoogleCseAdapter"]
