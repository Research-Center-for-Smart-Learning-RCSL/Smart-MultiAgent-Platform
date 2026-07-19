"""GET /api/knowmap-configs/{id}/graph — mirrors graphrag.py's read_graph (Phase 3β, AC-1).

Calls the route function directly (as test_knowmap_authz.py does for this
module's other helpers), patching KnowledgeFacade rather than a bare
service — the route goes through the facade (code review, 2026-07-10), matching
graphrag.py's read_graph structurally.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.knowmap import read_knowmap_graph
from contexts.knowledge.application.knowmap_graph_service import (
    KnowmapGraphEdge,
    KnowmapGraphNode,
    KnowmapGraphView,
)
from contexts.knowledge.domain.errors import KnowmapConfigNotFound
from contexts.knowledge.domain.graphrag import IN_FLIGHT_BUILD_STATES, BuildState

_PROJECT_ID = uuid.uuid4()


def _principal(*, is_admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(is_admin=is_admin, user_id=uuid.uuid4())


def _cfg(state: BuildState = BuildState.IDLE) -> SimpleNamespace:
    return SimpleNamespace(project_id=_PROJECT_ID, last_build_state=state)


def _fake_facade(*, cfg: SimpleNamespace | None, view: KnowmapGraphView | None) -> MagicMock:
    facade = MagicMock()
    facade.return_value.get_knowmap_config = AsyncMock(return_value=cfg)
    facade.return_value.get_knowmap_graph = AsyncMock(return_value=view)
    return facade


@pytest.mark.asyncio
async def test_read_knowmap_graph_returns_bounded_view() -> None:
    config_id = uuid.uuid4()
    view = KnowmapGraphView(
        config_id=config_id,
        project_id=_PROJECT_ID,
        nodes=(KnowmapGraphNode(name="alice", degree=1, build_id="b1", type="person"),),
        edges=(KnowmapGraphEdge(source="alice", relation="knows", target="bob", confidence=0.8),),
        truncated=True,
    )
    facade = _fake_facade(cfg=_cfg(), view=view)

    with (
        patch("app.api.v1.knowmap.KnowledgeFacade", facade),
        patch("app.api.v1.knowmap.assert_project_membership", AsyncMock()),
    ):
        out = await read_knowmap_graph(
            config_id=config_id,
            limit=500,
            principal=_principal(),
            db=AsyncMock(),
        )

    assert out.config_id == config_id
    assert out.truncated is True
    assert [n.id for n in out.nodes] == ["alice"]
    assert out.edges[0].source == "alice"
    assert out.edges[0].target == "bob"
    facade.return_value.get_knowmap_graph.assert_awaited_once_with(config_id, limit=500)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", sorted(IN_FLIGHT_BUILD_STATES, key=lambda s: s.value))
async def test_read_knowmap_graph_gated_in_unreadable_states(state: BuildState) -> None:
    """The visualizer honours the same read gate as turn-context retrieval.

    2026-07-17-graphrag-reset-expired-recovery AC-4: a mid-2PC or irrecoverable graph
    must not reach a viewer through this route either. The Neo4j read is skipped
    entirely, so a blocked view costs nothing.
    """
    config_id = uuid.uuid4()
    facade = _fake_facade(cfg=_cfg(state), view=None)

    with (
        patch("app.api.v1.knowmap.KnowledgeFacade", facade),
        patch("app.api.v1.knowmap.assert_project_membership", AsyncMock()),
    ):
        out = await read_knowmap_graph(
            config_id=config_id,
            limit=500,
            principal=_principal(),
            db=AsyncMock(),
        )

    assert out.nodes == []
    assert out.edges == []
    assert out.truncated is False
    facade.return_value.get_knowmap_graph.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [BuildState.IDLE, BuildState.QDRANT_COMMITTED, BuildState.FAILED])
async def test_read_knowmap_graph_served_in_readable_states(state: BuildState) -> None:
    """Ordinary FAILED stays readable: the last good graph is intact (AC-4 guard)."""
    config_id = uuid.uuid4()
    view = KnowmapGraphView(
        config_id=config_id,
        project_id=_PROJECT_ID,
        nodes=(KnowmapGraphNode(name="alice", degree=1, build_id="b1", type="person"),),
        edges=(),
        truncated=False,
    )
    facade = _fake_facade(cfg=_cfg(state), view=view)

    with (
        patch("app.api.v1.knowmap.KnowledgeFacade", facade),
        patch("app.api.v1.knowmap.assert_project_membership", AsyncMock()),
    ):
        out = await read_knowmap_graph(
            config_id=config_id,
            limit=500,
            principal=_principal(),
            db=AsyncMock(),
        )

    assert [n.id for n in out.nodes] == ["alice"]
    facade.return_value.get_knowmap_graph.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_knowmap_graph_missing_config_raises_not_found() -> None:
    config_id = uuid.uuid4()
    facade = _fake_facade(cfg=None, view=None)

    with (
        patch("app.api.v1.knowmap.KnowledgeFacade", facade),
        patch("app.api.v1.knowmap.assert_project_membership", AsyncMock()) as membership,
        pytest.raises(KnowmapConfigNotFound),
    ):
        await read_knowmap_graph(
            config_id=config_id,
            limit=500,
            principal=_principal(),
            db=AsyncMock(),
        )

    membership.assert_not_awaited()
    facade.return_value.get_knowmap_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_knowmap_graph_forbidden_for_non_member() -> None:
    config_id = uuid.uuid4()
    facade = _fake_facade(cfg=_cfg(), view=None)

    async def _deny(**_kw) -> None:
        raise HTTPException(status_code=403, detail="forbidden")

    with (
        patch("app.api.v1.knowmap.KnowledgeFacade", facade),
        patch("app.api.v1.knowmap.assert_project_membership", _deny),
        pytest.raises(HTTPException) as exc,
    ):
        await read_knowmap_graph(
            config_id=config_id,
            limit=500,
            principal=_principal(),
            db=AsyncMock(),
        )

    assert exc.value.status_code == 403
    facade.return_value.get_knowmap_graph.assert_not_awaited()
