"""The build-state read gate on the graph visualizer services (AC-4).

The gate lives in the services, not the routes, so every caller of
``KnowledgeFacade.get_*_graph`` inherits it -- a route test mocking the facade would
prove nothing about that. Both products are covered here because they must agree:
``IN_FLIGHT_BUILD_STATES`` is one frozenset, and a gated view must be distinguishable
from a genuinely empty graph on both.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from contexts.knowledge.application.graphrag_graph_service import GraphRagGraphService
from contexts.knowledge.application.knowmap_graph_service import KnowmapGraphService
from contexts.knowledge.domain.graphrag import IN_FLIGHT_BUILD_STATES, BuildState

_READABLE = [BuildState.IDLE, BuildState.QDRANT_COMMITTED, BuildState.FAILED]
_BLOCKED = sorted(IN_FLIGHT_BUILD_STATES, key=lambda s: s.value)


class FakeConfigRepo:
    def __init__(self, state: BuildState) -> None:
        self.cfg = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), last_build_state=state)

    async def get(self, _id: uuid.UUID, *, include_deleted: bool = False) -> Any:
        return self.cfg


def _graphrag(state: BuildState) -> tuple[GraphRagGraphService, FakeConfigRepo]:
    svc = GraphRagGraphService(None)  # type: ignore[arg-type]
    repo = FakeConfigRepo(state)
    svc._configs = repo  # type: ignore[assignment]
    return svc, repo


def _knowmap(state: BuildState) -> tuple[KnowmapGraphService, FakeConfigRepo]:
    svc = KnowmapGraphService(None)  # type: ignore[arg-type]
    repo = FakeConfigRepo(state)
    svc._configs = repo  # type: ignore[assignment]
    return svc, repo


@pytest.mark.asyncio
@pytest.mark.parametrize("state", _BLOCKED)
@pytest.mark.parametrize("product", ["graphrag", "knowmap"])
async def test_blocked_states_return_an_empty_flagged_view(state: BuildState, product: str) -> None:
    """No Neo4j driver is constructed, and the emptiness is labelled as gating.

    The services build their driver from settings immediately after this check, so a
    gate that did not return early would raise here rather than silently pass -- which
    is what makes "no store read happened" observable without patching the driver.
    """
    svc, repo = _graphrag(state) if product == "graphrag" else _knowmap(state)

    view = await svc.get_graph(config_id=repo.cfg.id, limit=500)

    assert view.nodes == ()
    assert view.edges == ()
    assert view.truncated is False
    assert view.build_state_blocked is True
    assert view.project_id == repo.cfg.project_id


@pytest.mark.asyncio
@pytest.mark.parametrize("state", _READABLE)
@pytest.mark.parametrize("product", ["graphrag", "knowmap"])
async def test_readable_states_are_not_gated(state: BuildState, product: str, monkeypatch: Any) -> None:
    """Ordinary FAILED stays readable: the last good graph is intact (AC-4 guard)."""
    svc, repo = _graphrag(state) if product == "graphrag" else _knowmap(state)

    module = type(svc).__module__
    fetched: list[uuid.UUID] = []

    class _Driver:
        def __init__(self, **_kw: Any) -> None: ...

        async def fetch_graph(self, *, config_id: uuid.UUID, limit: int) -> dict[str, Any]:
            fetched.append(config_id)
            return {"nodes": [], "edges": []}

        async def close(self) -> None: ...

    monkeypatch.setattr(f"{module}.Neo4jAsyncDriver", _Driver)

    view = await svc.get_graph(config_id=repo.cfg.id, limit=500)

    assert fetched == [repo.cfg.id], "a readable state must reach the store"
    assert view.build_state_blocked is False
