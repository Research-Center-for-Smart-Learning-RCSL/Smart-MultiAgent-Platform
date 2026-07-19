"""GET /api/graphrag-configs/{id}/graph — the Concept Map visualizer read gate.

Mirrors test_knowmap_graph_endpoint.py: calls the route function directly and patches
KnowledgeFacade, since the route assembles its read model through the facade.

Added with 2026-07-17-graphrag-reset-expired-recovery (AC-4). Before it, this route had
no build-state check at all, so a graph that turn-context retrieval refused to serve was
still fully visible here.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.graphrag import read_graph
from contexts.knowledge.domain.graphrag import IN_FLIGHT_BUILD_STATES, BuildState

_PROJECT_ID = uuid.uuid4()


def _principal() -> SimpleNamespace:
    return SimpleNamespace(is_admin=False, user_id=uuid.uuid4())


def _cfg(state: BuildState = BuildState.IDLE) -> SimpleNamespace:
    return SimpleNamespace(project_id=_PROJECT_ID, last_build_state=state)


def _view(config_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        config_id=config_id,
        nodes=(SimpleNamespace(name="alice", degree=1, build_id="b1", type="person"),),
        edges=(),
        truncated=False,
    )


def _fake_facade(*, cfg: SimpleNamespace | None, view: SimpleNamespace | None) -> MagicMock:
    facade = MagicMock()
    facade.return_value.get_graphrag_config = AsyncMock(return_value=cfg)
    facade.return_value.get_graphrag_graph = AsyncMock(return_value=view)
    return facade


@pytest.mark.asyncio
@pytest.mark.parametrize("state", sorted(IN_FLIGHT_BUILD_STATES, key=lambda s: s.value))
async def test_read_graph_gated_in_unreadable_states(state: BuildState) -> None:
    """Mid-2PC or irrecoverable: empty view, and no Neo4j read is attempted."""
    config_id = uuid.uuid4()
    facade = _fake_facade(cfg=_cfg(state), view=None)

    with (
        patch("app.api.v1.graphrag.KnowledgeFacade", facade),
        patch("app.api.v1.graphrag._assert_config_read", AsyncMock()),
    ):
        out = await read_graph(
            config_id=config_id,
            limit=500,
            principal=_principal(),
            db=AsyncMock(),
        )

    assert out.config_id == config_id
    assert out.nodes == []
    assert out.edges == []
    assert out.truncated is False
    facade.return_value.get_graphrag_graph.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [BuildState.IDLE, BuildState.QDRANT_COMMITTED, BuildState.FAILED])
async def test_read_graph_served_in_readable_states(state: BuildState) -> None:
    """Ordinary FAILED stays readable: the last good graph is intact (AC-4 guard)."""
    config_id = uuid.uuid4()
    facade = _fake_facade(cfg=_cfg(state), view=_view(config_id))

    with (
        patch("app.api.v1.graphrag.KnowledgeFacade", facade),
        patch("app.api.v1.graphrag._assert_config_read", AsyncMock()),
    ):
        out = await read_graph(
            config_id=config_id,
            limit=500,
            principal=_principal(),
            db=AsyncMock(),
        )

    assert [n.id for n in out.nodes] == ["alice"]
    facade.return_value.get_graphrag_graph.assert_awaited_once_with(config_id, limit=500)


@pytest.mark.asyncio
async def test_read_graph_gate_runs_after_authz() -> None:
    """A non-member must be refused before learning anything about the config's state."""
    config_id = uuid.uuid4()
    facade = _fake_facade(cfg=_cfg(BuildState.RECOVERY_UNAVAILABLE), view=None)

    async def _deny(**_kw: object) -> None:
        raise PermissionError("forbidden")

    with (
        patch("app.api.v1.graphrag.KnowledgeFacade", facade),
        patch("app.api.v1.graphrag._assert_config_read", _deny),
        pytest.raises(PermissionError),
    ):
        await read_graph(
            config_id=config_id,
            limit=500,
            principal=_principal(),
            db=AsyncMock(),
        )

    facade.return_value.get_graphrag_graph.assert_not_awaited()
