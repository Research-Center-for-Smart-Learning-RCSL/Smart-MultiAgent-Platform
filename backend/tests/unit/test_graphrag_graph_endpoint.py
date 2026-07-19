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
from contexts.knowledge.domain.graphrag import BuildState

_PROJECT_ID = uuid.uuid4()


def _principal() -> SimpleNamespace:
    return SimpleNamespace(is_admin=False, user_id=uuid.uuid4())


def _cfg(state: BuildState = BuildState.IDLE) -> SimpleNamespace:
    return SimpleNamespace(project_id=_PROJECT_ID, last_build_state=state)


def _view(config_id: uuid.UUID, *, blocked: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        config_id=config_id,
        nodes=() if blocked else (SimpleNamespace(name="alice", degree=1, build_id="b1", type="person"),),
        edges=(),
        truncated=False,
        build_state_blocked=blocked,
    )


def _fake_facade(*, cfg: SimpleNamespace | None, view: SimpleNamespace | None) -> MagicMock:
    facade = MagicMock()
    facade.return_value.get_graphrag_config = AsyncMock(return_value=cfg)
    facade.return_value.get_graphrag_graph = AsyncMock(return_value=view)
    return facade


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", [False, True])
async def test_read_graph_passes_the_gate_flag_through(blocked: bool) -> None:
    """The route must surface build_state_blocked, or the client cannot explain an
    empty graph. The gate itself is tested at the service level
    (test_graph_read_gate.py), which is where it lives.
    """
    config_id = uuid.uuid4()
    facade = _fake_facade(cfg=_cfg(), view=_view(config_id, blocked=blocked))

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

    assert out.build_state_blocked is blocked
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
