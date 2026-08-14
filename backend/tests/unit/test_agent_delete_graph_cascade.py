"""AC-6 — deleting an agent cascades its GraphRAG external-store purge.

Exercises the ``delete_agent`` route orchestration directly with all infra
mocked: enumerate the agent's GraphRAG configs, soft-delete them, commit
(DOM-4: the point of no return), then best-effort purge each config's Neo4j
subgraph + Qdrant vectors and emit a follow-up audit row per config.

Guards against the graph-leak regression where deleting an agent left its
GraphRAG Neo4j subgraph and Qdrant entity vectors orphaned.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_delete_agent_cascades_graphrag_external_stores() -> None:
    from app.api.v1 import agents as agents_mod

    project_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    cfg_ids = [uuid.uuid4(), uuid.uuid4()]
    configs = [
        SimpleNamespace(
            id=c,
            project_id=project_id,
            owner_kind="agent_group",
            owner_agent_group_id=uuid.uuid4(),
            owner_chatroom_id=None,
            owner_workspace_id=None,
        )
        for c in cfg_ids
    ]

    # A single ordered log proves DOM-4: commit trails the soft-deletes and
    # precedes every destructive external-store purge.
    call_log: list[str] = []

    db = MagicMock()
    db.commit = AsyncMock(side_effect=lambda: call_log.append("commit"))

    svc = MagicMock()
    svc.get = AsyncMock(return_value=SimpleNamespace(project_id=project_id))
    svc.soft_delete = AsyncMock()

    facade = MagicMock()
    facade.list_graph_configs_for_agent = AsyncMock(return_value=configs)

    async def _soft_delete_cfg(config_id: uuid.UUID, **_: Any) -> None:
        call_log.append(f"soft:{config_id}")

    facade.soft_delete_graph_config = AsyncMock(side_effect=_soft_delete_cfg)

    facade_cls = MagicMock(return_value=facade)

    async def _purge(*, config_id: uuid.UUID, project_id: uuid.UUID) -> dict[str, bool]:
        call_log.append(f"purge:{config_id}")
        return {"neo4j_purged": True, "qdrant_purged": True}

    facade_cls.purge_graph_config_external_stores = AsyncMock(side_effect=_purge)

    emitted: list[Any] = []

    async def _emit(_db: Any, event: Any) -> None:
        emitted.append(event)

    ctx = SimpleNamespace(actor_ip="1.2.3.4", request_id=uuid.uuid4())
    principal = SimpleNamespace(user_id=uuid.uuid4())

    with (
        patch.object(agents_mod, "AgentService", MagicMock(return_value=svc)),
        patch(
            "contexts.knowledge.interfaces.facade.KnowledgeFacade",
            facade_cls,
        ),
        patch(
            "shared_kernel.auth.dependencies.get_role_resolver",
            AsyncMock(return_value=object()),
        ),
        patch(
            "shared_kernel.auth.permissions.decide",
            AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ),
        patch("shared_kernel.audit.emit", AsyncMock(side_effect=_emit)),
    ):
        await agents_mod.delete_agent(
            agent_id=agent_id,
            if_match='"7"',
            ctx=ctx,  # type: ignore[arg-type]
            principal=principal,  # type: ignore[arg-type]
            db=db,
        )

    # The agent itself is soft-deleted with the parsed optimistic-lock version.
    assert svc.soft_delete.await_count == 1
    assert svc.soft_delete.await_args.kwargs["expected_version"] == 7

    # Every owned config is enumerated and soft-deleted through the service
    # surface (which emits the canonical graphrag.deleted audit), with actor
    # context threaded so the audit trail is attributable.
    facade.list_graph_configs_for_agent.assert_awaited_once_with(agent_id)
    assert facade.soft_delete_graph_config.await_count == len(cfg_ids)
    for call in facade.soft_delete_graph_config.await_args_list:
        assert call.kwargs["actor_user_id"] == principal.user_id
        assert call.kwargs["request_id"] == ctx.request_id

    # DOM-4 ordering: commit sits after both soft-deletes and before any purge.
    commit_at = call_log.index("commit")
    assert all(call_log.index(f"soft:{c}") < commit_at for c in cfg_ids)
    assert all(call_log.index(f"purge:{c}") > commit_at for c in cfg_ids)

    # Each config's external stores are purged with its own project scope.
    assert facade_cls.purge_graph_config_external_stores.await_count == len(cfg_ids)
    purged = {
        call.kwargs["config_id"]: call.kwargs["project_id"]
        for call in facade_cls.purge_graph_config_external_stores.await_args_list
    }
    assert purged == dict.fromkeys(cfg_ids, project_id)

    # A durable audit row follows each purge, carrying its outcome.
    assert [e.action for e in emitted] == ["graphrag.config_infra_purged"] * len(cfg_ids)
    for event in emitted:
        assert event.metadata["project_id"] == str(project_id)
        assert event.metadata["agent_id"] == str(agent_id)
        assert event.metadata["neo4j_purged"] is True
        assert event.metadata["qdrant_purged"] is True


@pytest.mark.asyncio
async def test_delete_agent_without_graph_configs_skips_purge() -> None:
    """No GraphRAG configs -> no purge, no extra audit rows (still commits)."""
    from app.api.v1 import agents as agents_mod

    project_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    db = MagicMock()
    db.commit = AsyncMock()

    svc = MagicMock()
    svc.get = AsyncMock(return_value=SimpleNamespace(project_id=project_id))
    svc.soft_delete = AsyncMock()

    facade = MagicMock()
    facade.list_graph_configs_for_agent = AsyncMock(return_value=[])
    facade.soft_delete_graph_config = AsyncMock()
    facade_cls = MagicMock(return_value=facade)
    facade_cls.purge_graph_config_external_stores = AsyncMock()

    emitted: list[Any] = []

    async def _emit(_db: Any, event: Any) -> None:
        emitted.append(event)

    ctx = SimpleNamespace(actor_ip=None, request_id=uuid.uuid4())
    principal = SimpleNamespace(user_id=uuid.uuid4())

    with (
        patch.object(agents_mod, "AgentService", MagicMock(return_value=svc)),
        patch("contexts.knowledge.interfaces.facade.KnowledgeFacade", facade_cls),
        patch(
            "shared_kernel.auth.dependencies.get_role_resolver",
            AsyncMock(return_value=object()),
        ),
        patch(
            "shared_kernel.auth.permissions.decide",
            AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ),
        patch("shared_kernel.audit.emit", AsyncMock(side_effect=_emit)),
    ):
        await agents_mod.delete_agent(
            agent_id=agent_id,
            if_match='"1"',
            ctx=ctx,  # type: ignore[arg-type]
            principal=principal,  # type: ignore[arg-type]
            db=db,
        )

    svc.soft_delete.assert_awaited_once()
    db.commit.assert_awaited_once()
    facade.soft_delete_graph_config.assert_not_awaited()
    facade_cls.purge_graph_config_external_stores.assert_not_awaited()
    assert emitted == []
