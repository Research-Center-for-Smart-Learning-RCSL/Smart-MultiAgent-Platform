"""WS6 (R11.20/AC-10) — deleting a chatroom/workspace/agent_group purges its
owned Concept Map's external stores.

Two layers of coverage:
  * the shared cascade helper (`_graphrag_owner_cascade`) — the owner-agnostic
    enumerate/soft-delete and post-commit purge/audit both halves the three
    delete routes reuse;
  * the chatroom delete route — the DOM-4 ordering guard (commit sits between the
    soft-deletes and every destructive external-store purge).

Guards against the graph-leak regression where deleting an owner left its Neo4j
subgraph and Qdrant entity vectors orphaned.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_soft_delete_owner_configs_enumerates_and_soft_deletes() -> None:
    from app.api.v1 import _graphrag_owner_cascade as mod

    owner_id = uuid.uuid4()
    project_id = uuid.uuid4()
    cfg_ids = [uuid.uuid4(), uuid.uuid4()]
    configs = [SimpleNamespace(id=c, project_id=project_id) for c in cfg_ids]

    facade = MagicMock()
    facade.list_graph_configs_for_owner = AsyncMock(return_value=configs)
    facade.soft_delete_graph_config = AsyncMock()

    request_id = uuid.uuid4()
    actor_user_id = uuid.uuid4()

    out = await mod.soft_delete_owner_graph_configs(
        facade,
        owner_kind="agent_group",
        owner_id=owner_id,
        actor_user_id=actor_user_id,
        actor_ip="1.2.3.4",
        request_id=request_id,
    )

    facade.list_graph_configs_for_owner.assert_awaited_once_with(owner_kind="agent_group", owner_id=owner_id)
    assert list(out) == configs
    assert facade.soft_delete_graph_config.await_count == len(cfg_ids)
    for call in facade.soft_delete_graph_config.await_args_list:
        assert call.kwargs["actor_user_id"] == actor_user_id
        assert call.kwargs["request_id"] == request_id


@pytest.mark.asyncio
async def test_soft_delete_owner_configs_empty_skips_soft_delete() -> None:
    from app.api.v1 import _graphrag_owner_cascade as mod

    facade = MagicMock()
    facade.list_graph_configs_for_owner = AsyncMock(return_value=[])
    facade.soft_delete_graph_config = AsyncMock()

    out = await mod.soft_delete_owner_graph_configs(
        facade,
        owner_kind="workspace",
        owner_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
        request_id=None,
    )

    assert list(out) == []
    facade.soft_delete_graph_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_owner_configs_audits_with_owner_context() -> None:
    from app.api.v1 import _graphrag_owner_cascade as mod

    owner_id = uuid.uuid4()
    project_id = uuid.uuid4()
    cfg_ids = [uuid.uuid4(), uuid.uuid4()]
    configs = [
        SimpleNamespace(
            id=c,
            project_id=project_id,
            owner_kind="workspace",
            owner_workspace_id=owner_id,
            owner_chatroom_id=None,
            owner_agent_group_id=None,
        )
        for c in cfg_ids
    ]

    async def _purge(*, config_id: uuid.UUID, project_id: uuid.UUID) -> dict[str, bool]:
        return {"neo4j_purged": True, "qdrant_purged": True}

    facade_cls = MagicMock()
    facade_cls.purge_graph_config_external_stores = AsyncMock(side_effect=_purge)

    emitted: list[Any] = []

    async def _emit(_db: Any, event: Any) -> None:
        emitted.append(event)

    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    with (
        patch.object(mod, "KnowledgeFacade", facade_cls),
        patch("shared_kernel.audit.emit", AsyncMock(side_effect=_emit)),
    ):
        await mod.purge_owner_graph_configs_external(
            db,
            configs=configs,
            actor_user_id=uuid.uuid4(),
            actor_ip="1.2.3.4",
            request_id=uuid.uuid4(),
        )

    assert facade_cls.purge_graph_config_external_stores.await_count == len(cfg_ids)
    assert [e.action for e in emitted] == ["graphrag.config_infra_purged"] * len(cfg_ids)
    for event in emitted:
        assert event.metadata["owner_kind"] == "workspace"
        assert event.metadata["owner_id"] == str(owner_id)
        assert event.metadata["project_id"] == str(project_id)
        assert event.metadata["neo4j_purged"] is True
        assert event.metadata["qdrant_purged"] is True
    # Each config's audit row is committed on its own so a later failure can't
    # discard an already-recorded sibling.
    assert db.commit.await_count == len(cfg_ids)
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_owner_configs_isolates_a_failing_config() -> None:
    """A purge/audit failure on one config rolls back and continues to siblings,
    never turning a committed owner delete into a 500."""
    from app.api.v1 import _graphrag_owner_cascade as mod

    project_id = uuid.uuid4()
    ok_id, bad_id = uuid.uuid4(), uuid.uuid4()
    configs = [
        SimpleNamespace(
            id=bad_id,
            project_id=project_id,
            owner_kind="chatroom",
            owner_chatroom_id=uuid.uuid4(),
            owner_agent_group_id=None,
            owner_workspace_id=None,
        ),
        SimpleNamespace(
            id=ok_id,
            project_id=project_id,
            owner_kind="chatroom",
            owner_chatroom_id=uuid.uuid4(),
            owner_agent_group_id=None,
            owner_workspace_id=None,
        ),
    ]

    async def _purge(*, config_id: uuid.UUID, project_id: uuid.UUID) -> dict[str, bool]:
        if config_id == bad_id:
            raise RuntimeError("neo4j unreachable")
        return {"neo4j_purged": True, "qdrant_purged": True}

    facade_cls = MagicMock()
    facade_cls.purge_graph_config_external_stores = AsyncMock(side_effect=_purge)

    emitted: list[Any] = []

    async def _emit(_db: Any, event: Any) -> None:
        emitted.append(event)

    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    with (
        patch.object(mod, "KnowledgeFacade", facade_cls),
        patch("shared_kernel.audit.emit", AsyncMock(side_effect=_emit)),
    ):
        await mod.purge_owner_graph_configs_external(
            db,
            configs=configs,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            request_id=None,
        )

    # The good config still gets its own committed audit row; the failing one is
    # isolated to its own rollback and never discards the sibling.
    assert [e.metadata["project_id"] for e in emitted] == [str(project_id)]
    db.commit.assert_awaited_once()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_chatroom_cascade_dom4_ordering() -> None:
    """The chatroom delete route soft-deletes the owned configs + room, commits,
    then purges — proving the DOM-4 boundary at the integration point."""
    from app.api.v1 import chatrooms as rooms_mod

    chatroom_id = uuid.uuid4()
    project_id = uuid.uuid4()
    configs = [SimpleNamespace(id=uuid.uuid4(), project_id=project_id)]

    call_log: list[str] = []

    db = MagicMock()
    db.commit = AsyncMock(side_effect=lambda: call_log.append("commit"))

    svc = MagicMock()

    async def _room_soft_delete(**_: Any) -> None:
        call_log.append("room_soft_delete")

    svc.soft_delete = AsyncMock(side_effect=_room_soft_delete)

    async def _soft(*_a: Any, **_k: Any) -> list[Any]:
        call_log.append("configs_soft_delete")
        return configs

    async def _purge(*_a: Any, **_k: Any) -> None:
        call_log.append("purge")

    ctx = SimpleNamespace(actor_ip="1.2.3.4", request_id=uuid.uuid4())
    principal = SimpleNamespace(user_id=uuid.uuid4())

    with (
        patch.object(
            rooms_mod,
            "_project_id_for_chatroom",
            AsyncMock(return_value=project_id),
        ),
        patch.object(rooms_mod, "_require_project_cap", AsyncMock()),
        patch.object(rooms_mod, "ChatroomService", MagicMock(return_value=svc)),
        patch(
            "app.api.v1._graphrag_owner_cascade.soft_delete_owner_graph_configs",
            AsyncMock(side_effect=_soft),
        ),
        patch(
            "app.api.v1._graphrag_owner_cascade.purge_owner_graph_configs_external",
            AsyncMock(side_effect=_purge),
        ),
        patch("contexts.knowledge.interfaces.facade.KnowledgeFacade", MagicMock()),
    ):
        await rooms_mod.delete_chatroom(
            chatroom_id=chatroom_id,
            ctx=ctx,  # type: ignore[arg-type]
            principal=principal,  # type: ignore[arg-type]
            db=db,
        )

    commit_at = call_log.index("commit")
    assert call_log.index("configs_soft_delete") < commit_at
    assert call_log.index("room_soft_delete") < commit_at
    assert call_log.index("purge") > commit_at
