"""Search-provider probes (D.11, mirrors D.3).

Each probe hits a minimal-surface endpoint — the same shape the agent tool
will use in Phase E. A green probe verifies the credential authenticates
against the provider; it does NOT guarantee the downstream tool call will
succeed, because the probe calls the provider directly and the tool call
egresses through the project's Egress Proxy allowlist (R12.16), which is
seeded only on key activation — after this probe runs. Configurable fields
(``google_cse.cx``, ``tavily.search_depth``) travel through the probe via
`config`.
"""

from __future__ import annotations

from typing import Any

import httpx

from contexts.keys.domain.search import SEARCH_PROVIDER_HOSTS, SearchProvider
from contexts.keys.infrastructure.probes.base import (
    ProbeResult,
    new_http_client,
    summarise_http_failure,
)


async def _probe_brave(secret: str, _config: dict[str, Any]) -> ProbeResult:
    url = f"https://{SEARCH_PROVIDER_HOSTS[SearchProvider.BRAVE]}/res/v1/web/search"
    try:
        async with new_http_client() as c:
            resp = await c.get(
                url,
                headers={"X-Subscription-Token": secret, "Accept": "application/json"},
                params={"q": "ping", "count": 1},
            )
    except httpx.HTTPError as exc:
        return ProbeResult.failed(f"network: {exc.__class__.__name__}")
    if resp.status_code == 200:
        return ProbeResult.ok()
    if resp.status_code in (401, 403):
        return ProbeResult.failed("unauthorized")
    return ProbeResult.failed(summarise_http_failure(resp))


async def _probe_serper(secret: str, _config: dict[str, Any]) -> ProbeResult:
    url = f"https://{SEARCH_PROVIDER_HOSTS[SearchProvider.SERPER]}/search"
    try:
        async with new_http_client() as c:
            resp = await c.post(
                url,
                headers={"X-API-KEY": secret, "content-type": "application/json"},
                json={"q": "ping", "num": 1},
            )
    except httpx.HTTPError as exc:
        return ProbeResult.failed(f"network: {exc.__class__.__name__}")
    if resp.status_code == 200:
        return ProbeResult.ok()
    if resp.status_code in (401, 403):
        return ProbeResult.failed("unauthorized")
    return ProbeResult.failed(summarise_http_failure(resp))


async def _probe_tavily(secret: str, config: dict[str, Any]) -> ProbeResult:
    url = f"https://{SEARCH_PROVIDER_HOSTS[SearchProvider.TAVILY]}/search"
    depth = config.get("search_depth", "basic")
    try:
        async with new_http_client() as c:
            resp = await c.post(
                url,
                headers={"content-type": "application/json"},
                json={"api_key": secret, "query": "ping", "max_results": 1, "search_depth": depth},
            )
    except httpx.HTTPError as exc:
        return ProbeResult.failed(f"network: {exc.__class__.__name__}")
    if resp.status_code == 200:
        return ProbeResult.ok()
    if resp.status_code in (401, 403):
        return ProbeResult.failed("unauthorized")
    return ProbeResult.failed(summarise_http_failure(resp))


async def _probe_google_cse(secret: str, config: dict[str, Any]) -> ProbeResult:
    cx = config.get("cx")
    if not cx:
        return ProbeResult.failed("missing_cx")
    url = f"https://{SEARCH_PROVIDER_HOSTS[SearchProvider.GOOGLE_CSE]}/customsearch/v1"
    try:
        async with new_http_client() as c:
            resp = await c.get(url, params={"key": secret, "cx": cx, "q": "ping", "num": 1})
    except httpx.HTTPError as exc:
        return ProbeResult.failed(f"network: {exc.__class__.__name__}")
    if resp.status_code == 200:
        return ProbeResult.ok()
    if resp.status_code in (401, 403):
        return ProbeResult.failed("unauthorized")
    return ProbeResult.failed(summarise_http_failure(resp))


SEARCH_PROBES = {
    SearchProvider.BRAVE: _probe_brave,
    SearchProvider.SERPER: _probe_serper,
    SearchProvider.TAVILY: _probe_tavily,
    SearchProvider.GOOGLE_CSE: _probe_google_cse,
}


async def probe_search(provider: SearchProvider, secret: str, config: dict[str, Any]) -> ProbeResult:
    return await SEARCH_PROBES[provider](secret, config)


__all__ = ["SEARCH_PROBES", "probe_search"]
