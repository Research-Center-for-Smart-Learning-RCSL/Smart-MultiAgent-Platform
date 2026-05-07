"""BYO search adapter registry (R12.17).

``build_registry()`` returns the ``{SearchProvider: SearchAdapter}`` mapping
that :class:`WebSearchTool` consumes. Only Tavily is wired end-to-end for v1;
adding a new provider is a matter of dropping an adapter module in here and
extending ``_ADAPTER_FACTORIES``.
"""

from __future__ import annotations

from collections.abc import Callable

from contexts.agents.application.mcp_ports import SearchAdapter
from contexts.agents.infrastructure.search_adapters.tavily import TavilyAdapter
from contexts.keys.domain.search import SearchProvider

_ADAPTER_FACTORIES: dict[SearchProvider, Callable[[], SearchAdapter]] = {
    SearchProvider.TAVILY: TavilyAdapter,  # type: ignore[dict-item]
}


def build_registry() -> dict[SearchProvider, SearchAdapter]:
    """Instantiate one adapter per registered provider.

    Intentionally eager — adapters are stateless factories and cheap to build.
    """
    return {provider: factory() for provider, factory in _ADAPTER_FACTORIES.items()}


__all__ = ["TavilyAdapter", "build_registry"]
