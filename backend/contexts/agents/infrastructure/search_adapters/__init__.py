"""BYO search adapter registry (R12.17).

``build_registry()`` returns the ``{SearchProvider: SearchAdapter}`` mapping
that :class:`WebSearchTool` consumes. All four v1 providers are wired;
adding a new provider is a matter of dropping an adapter module in here and
extending ``_ADAPTER_FACTORIES``.
"""

from __future__ import annotations

from collections.abc import Callable

from contexts.agents.application.mcp_ports import SearchAdapter
from contexts.agents.infrastructure.search_adapters.brave import BraveAdapter
from contexts.agents.infrastructure.search_adapters.google_cse import GoogleCseAdapter
from contexts.agents.infrastructure.search_adapters.serper import SerperAdapter
from contexts.agents.infrastructure.search_adapters.tavily import TavilyAdapter
from contexts.keys.domain.search import SearchProvider

_ADAPTER_FACTORIES: dict[SearchProvider, Callable[[], SearchAdapter]] = {
    SearchProvider.BRAVE: BraveAdapter,  # type: ignore[dict-item]
    SearchProvider.SERPER: SerperAdapter,  # type: ignore[dict-item]
    SearchProvider.TAVILY: TavilyAdapter,  # type: ignore[dict-item]
    SearchProvider.GOOGLE_CSE: GoogleCseAdapter,  # type: ignore[dict-item]
}


def build_registry() -> dict[SearchProvider, SearchAdapter]:
    """Instantiate one adapter per registered provider.

    Intentionally eager — adapters are stateless factories and cheap to build.
    Per-key config (e.g. Google CSE ``cx``) is forwarded at call time via the
    ``config`` kwarg on ``SearchAdapter.search()``.
    """
    return {provider: factory() for provider, factory in _ADAPTER_FACTORIES.items()}


__all__ = [
    "BraveAdapter",
    "GoogleCseAdapter",
    "SerperAdapter",
    "TavilyAdapter",
    "build_registry",
]
