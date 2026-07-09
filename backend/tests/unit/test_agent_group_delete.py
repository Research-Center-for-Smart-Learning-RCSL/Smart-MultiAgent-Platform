"""WS6 (R11.20/AC-10) — agent_group delete: service audit + route cascade.

The agent_group owner kind had no delete path before WS6; these lock the new
one. The service tombstones + audits (and maps a delete race to NotFound); the
route drives the shared GraphRAG purge cascade with the DOM-4 commit boundary.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.agent_groups.domain.errors import AgentGroupNotFound


@pytest.mark.asyncio
async def test_service_soft_delete_audits_and_returns_project() -> None:
    from contexts.agent_groups.application.group_service import AgentGroupService

    group_id = uuid.uuid4()
    project_id = uuid.uuid4()
    actor_user_id = uuid.uuid4()
    request_id = uuid.uuid4()

    service = AgentGroupService(MagicMock())
    service._repo = MagicMock()
    service._repo.project_id_of = AsyncMock(return_value=project_id)
    service._repo.soft_delete = AsyncMock(return_value=True)

    emitted: list[Any] = []

    async def _emit(_db: Any, event: Any) -> None:
        emitted.append(event)

    with patch("shared_kernel.audit.emit", AsyncMock(side_effect=_emit)):
        out = await service.soft_delete(
            group_id=group_id,
            actor_user_id=actor_user_id,
            actor_ip="1.2.3.4",
            request_id=request_id,
        )

    assert out == project_id
    service._repo.soft_delete.assert_awaited_once_with(group_id=group_id)
    assert [e.action for e in emitted] == ["agent_group.deleted"]
    assert emitted[0].metadata["project_id"] == str(project_id)
    assert emitted[0].resource_id == group_id


@pytest.mark.asyncio
async def test_service_soft_delete_race_raises_not_found_without_audit() -> None:
    from contexts.agent_groups.application.group_service import AgentGroupService

    group_id = uuid.uuid4()

    service = AgentGroupService(MagicMock())
    service._repo = MagicMock()
    service._repo.project_id_of = AsyncMock(return_value=uuid.uuid4())
    # A concurrent delete already tombstoned the row -> 0 rows updated.
    service._repo.soft_delete = AsyncMock(return_value=False)

    emitted: list[Any] = []

    async def _emit(_db: Any, event: Any) -> None:
        emitted.append(event)

    with (
        patch("shared_kernel.audit.emit", AsyncMock(side_effect=_emit)),
        pytest.raises(AgentGroupNotFound),
    ):
        await service.soft_delete(
            group_id=group_id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            request_id=None,
        )

    assert emitted == []


@pytest.mark.asyncio
async def test_delete_group_route_cascade_dom4_ordering() -> None:
    from app.api.v1 import agent_groups as groups_mod

    group_id = uuid.uuid4()
    project_id = uuid.uuid4()
    configs = [SimpleNamespace(id=uuid.uuid4(), project_id=project_id)]

    call_log: list[str] = []

    db = MagicMock()
    db.commit = AsyncMock(side_effect=lambda: call_log.append("commit"))

    facade = MagicMock()

    async def _group_soft_delete(**_: Any) -> uuid.UUID:
        call_log.append("group_soft_delete")
        return project_id

    facade.soft_delete = AsyncMock(side_effect=_group_soft_delete)

    async def _soft(*_a: Any, **_k: Any) -> list[Any]:
        call_log.append("configs_soft_delete")
        return configs

    async def _purge(*_a: Any, **_k: Any) -> None:
        call_log.append("purge")

    ctx = SimpleNamespace(actor_ip="1.2.3.4", request_id=uuid.uuid4())
    principal = SimpleNamespace(user_id=uuid.uuid4())

    with (
        patch.object(groups_mod, "_group_project_id", AsyncMock(return_value=project_id)),
        patch.object(groups_mod, "_assert_project_owner", AsyncMock()),
        patch.object(groups_mod, "AgentGroupFacade", MagicMock(return_value=facade)),
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
        await groups_mod.delete_group(
            group_id=group_id,
            ctx=ctx,  # type: ignore[arg-type]
            principal=principal,  # type: ignore[arg-type]
            db=db,
        )

    commit_at = call_log.index("commit")
    assert call_log.index("configs_soft_delete") < commit_at
    assert call_log.index("group_soft_delete") < commit_at
    assert call_log.index("purge") > commit_at
