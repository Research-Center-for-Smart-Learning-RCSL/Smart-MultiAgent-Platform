"""Pins that adapters and probes read the single authoritative
provider -> hostname map (contexts.keys.domain.search.SEARCH_PROVIDER_HOSTS)
instead of carrying their own copy, per R12.16's fix design part 2."""

from __future__ import annotations

from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

import pytest

from contexts.agents.infrastructure.search_adapters.brave import _BRAVE_ENDPOINT
from contexts.agents.infrastructure.search_adapters.google_cse import _CSE_ENDPOINT
from contexts.agents.infrastructure.search_adapters.serper import _SERPER_ENDPOINT
from contexts.agents.infrastructure.search_adapters.tavily import _TAVILY_ENDPOINT
from contexts.keys.domain.search import SEARCH_PROVIDER_HOSTS, SearchProvider

_ADAPTER_ENDPOINTS = {
    SearchProvider.BRAVE: _BRAVE_ENDPOINT,
    SearchProvider.SERPER: _SERPER_ENDPOINT,
    SearchProvider.TAVILY: _TAVILY_ENDPOINT,
    SearchProvider.GOOGLE_CSE: _CSE_ENDPOINT,
}


@pytest.mark.parametrize("provider", list(SearchProvider))
def test_adapter_endpoint_host_matches_map(provider: SearchProvider) -> None:
    endpoint = _ADAPTER_ENDPOINTS[provider]
    assert urlsplit(endpoint).hostname == SEARCH_PROVIDER_HOSTS[provider]


class _FakeResponse:
    status_code = 200

    def json(self) -> dict[str, Any]:
        return {}


class _FakeHttpClient:
    """Records the URL each probe requests; never performs real network I/O."""

    def __init__(self) -> None:
        self.requested_url: str | None = None

    async def __aenter__(self) -> _FakeHttpClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def get(self, url: str, **_: Any) -> _FakeResponse:
        self.requested_url = url
        return _FakeResponse()

    async def post(self, url: str, **_: Any) -> _FakeResponse:
        self.requested_url = url
        return _FakeResponse()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", list(SearchProvider))
async def test_probe_url_host_matches_map(monkeypatch: pytest.MonkeyPatch, provider: SearchProvider) -> None:
    import contexts.keys.infrastructure.search_probes as probes_module

    client = _FakeHttpClient()
    monkeypatch.setattr(probes_module, "new_http_client", lambda: client)

    config = {"cx": "fake-cx"} if provider is SearchProvider.GOOGLE_CSE else {}
    await probes_module.probe_search(provider, "fake-secret", config)

    assert client.requested_url is not None
    assert urlsplit(client.requested_url).hostname == SEARCH_PROVIDER_HOSTS[provider]
