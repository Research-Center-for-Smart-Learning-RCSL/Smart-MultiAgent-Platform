"""Serper (Google SERP) :class:`SearchAdapter` (R12.17).

Routes through :class:`EgressProxyClient` — the Serper API key is sent
via the ``X-API-KEY`` header.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from contexts.agents.application.mcp_ports import EgressProxyClient
from contexts.agents.domain.mcp import SearchResult

_SERPER_ENDPOINT = "https://google.serper.dev/search"

_FRESHNESS_MAP: dict[str, str | None] = {
    "any": None,
    "day": "qdr:d",
    "week": "qdr:w",
    "month": "qdr:m",
    "year": "qdr:y",
}


def _parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class SerperAdapter:
    name: str = "serper"

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
        body: dict[str, Any] = {
            "q": query,
            "num": int(top_k),
        }
        tbs = _FRESHNESS_MAP.get(freshness)
        if tbs is not None:
            body["tbs"] = tbs

        status, _headers, payload = await proxy.request(
            method="POST",
            url=_SERPER_ENDPOINT,
            project_id=project_id,
            headers={
                "X-API-KEY": api_key.decode("utf-8").strip(),
                "content-type": "application/json",
            },
            json_body=body,
        )
        if status >= 400:
            raise RuntimeError(
                f"serper returned HTTP {status}: {payload[:256]!r}",
            )
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"serper returned non-JSON: {exc}") from exc

        out: list[SearchResult] = []
        for idx, item in enumerate(parsed.get("organic", []) or []):
            if not isinstance(item, dict):
                continue
            out.append(
                SearchResult(
                    title=str(item.get("title", "")),
                    url=str(item.get("link", "")),
                    snippet=str(item.get("snippet", "")),
                    published_at=_parse_date(item.get("date")),
                    score=float(max(0.0, 1.0 - idx * 0.05)),
                )
            )
        return out


__all__ = ["SerperAdapter"]
