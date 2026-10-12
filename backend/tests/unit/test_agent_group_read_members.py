"""Per-member Concept Map read filter: service rules and route gate (R11.22, AC-2).

``docs/tasks/2026-10-12-graphrag-phase5b-member-filter`` §5.2. The Project Owner
sets, per member, whose contributions it reads from the group map; ``None`` is
the whole group. The SQL (``array_remove``, ``@>``) runs against real Postgres
in ``tests/wiring/test_agent_group_read_members.py``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from contexts.agent_groups.domain.errors import (
    AgentGroupMemberNotFound,
    AgentGroupReadMembersInvalid,
)
from contexts.agent_groups.domain.models import AgentGroupMember


def _service(members: list[uuid.UUID]) -> Any:
    from contexts.agent_groups.application.group_service import AgentGroupService

    service = AgentGroupService(MagicMock())
    service._repo = MagicMock()
    service._repo.project_id_of = AsyncMock(return_value=uuid.uuid4())
    service._repo.list_member_agent_ids = AsyncMock(return_value=members)
    service._repo.set_read_member_ids = AsyncMock(return_value=True)
    service._repo.remove_member = AsyncMock(return_value=True)
    service._repo.strip_read_member = AsyncMock(return_value=1)
    return service


async def _set(service: Any, agent_id: uuid.UUID, member_ids: Any) -> tuple[Any, list[Any]]:
    emitted: list[Any] = []

    async def _emit(_db: Any, event: Any) -> None:
        emitted.append(event)

    with patch("shared_kernel.audit.emit", AsyncMock(side_effect=_emit)):
        out = await service.set_read_members(
            group_id=uuid.uuid4(),
            agent_id=agent_id,
            member_ids=member_ids,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    return out, emitted


@pytest.mark.asyncio
async def test_a_member_can_be_restricted_and_the_list_is_normalised() -> None:
    a, b, c = sorted(uuid.uuid4() for _ in range(3))
    service = _service([a, b, c])
    out, emitted = await _set(service, a, [c, b, c])
    assert out == AgentGroupMember(agent_id=a, read_member_ids=(b, c))
    assert service._repo.set_read_member_ids.await_args.kwargs["read_member_ids"] == (b, c)
    assert [e.action for e in emitted] == ["agent_group.read_members_set"]
    assert emitted[0].metadata["read_member_ids"] == [str(b), str(c)]


@pytest.mark.asyncio
async def test_null_restores_the_whole_group() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    service = _service([a, b])
    out, emitted = await _set(service, a, None)
    assert out.read_member_ids is None
    assert service._repo.set_read_member_ids.await_args.kwargs["read_member_ids"] is None
    assert emitted[0].metadata["read_member_ids"] is None


@pytest.mark.asyncio
async def test_an_empty_list_is_refused() -> None:
    a = uuid.uuid4()
    service = _service([a])
    with pytest.raises(AgentGroupReadMembersInvalid):
        await _set(service, a, [])
    service._repo.set_read_member_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_id_outside_the_group_is_refused() -> None:
    # Covers another group's agent and another project's: neither is a live
    # member of this group, which is the only list accepted (tenant boundary).
    a, b = uuid.uuid4(), uuid.uuid4()
    service = _service([a, b])
    with pytest.raises(AgentGroupReadMembersInvalid):
        await _set(service, a, [b, uuid.uuid4()])
    service._repo.set_read_member_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_target_must_be_a_member() -> None:
    service = _service([uuid.uuid4()])
    with pytest.raises(AgentGroupMemberNotFound):
        await _set(service, uuid.uuid4(), None)


@pytest.mark.asyncio
async def test_removing_a_member_strips_it_from_every_filter() -> None:
    a = uuid.uuid4()
    group_id = uuid.uuid4()
    service = _service([a])
    with patch("shared_kernel.audit.emit", AsyncMock()):
        await service.remove_member(group_id=group_id, agent_id=a, actor_user_id=uuid.uuid4(), actor_ip=None)
    service._repo.strip_read_member.assert_awaited_once_with(group_id=group_id, agent_id=a)


@pytest.mark.asyncio
async def test_removing_a_non_member_strips_nothing() -> None:
    service = _service([])
    service._repo.remove_member = AsyncMock(return_value=False)
    with patch("shared_kernel.audit.emit", AsyncMock()):
        await service.remove_member(
            group_id=uuid.uuid4(), agent_id=uuid.uuid4(), actor_user_id=uuid.uuid4(), actor_ip=None
        )
    service._repo.strip_read_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_route_refuses_a_non_owner() -> None:
    from app.api.v1 import agent_groups as groups_mod

    facade = MagicMock()
    facade.set_read_members = AsyncMock()
    with (
        patch.object(groups_mod, "_group_project_id", AsyncMock(return_value=uuid.uuid4())),
        patch("app.api.v1.deps.is_project_owner_or_admin", AsyncMock(return_value=False)),
        patch.object(groups_mod, "AgentGroupFacade", MagicMock(return_value=facade)),
        pytest.raises(HTTPException) as caught,
    ):
        await groups_mod.set_read_members(
            body=groups_mod.ReadMembersIn(member_ids=None),
            group_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            ctx=SimpleNamespace(actor_ip=None, request_id=None),  # type: ignore[arg-type]
            principal=SimpleNamespace(user_id=uuid.uuid4()),  # type: ignore[arg-type]
            db=MagicMock(),
        )
    assert caught.value.status_code == 403
    facade.set_read_members.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_route_sets_and_returns_the_filter_for_an_owner() -> None:
    from app.api.v1 import agent_groups as groups_mod

    agent_id, b = uuid.uuid4(), uuid.uuid4()
    facade = MagicMock()
    facade.set_read_members = AsyncMock(
        return_value=AgentGroupMember(agent_id=agent_id, read_member_ids=(b,))
    )
    with (
        patch.object(groups_mod, "_group_project_id", AsyncMock(return_value=uuid.uuid4())),
        patch("app.api.v1.deps.is_project_owner_or_admin", AsyncMock(return_value=True)),
        patch.object(groups_mod, "AgentGroupFacade", MagicMock(return_value=facade)),
    ):
        out = await groups_mod.set_read_members(
            body=groups_mod.ReadMembersIn(member_ids=[b]),
            group_id=uuid.uuid4(),
            agent_id=agent_id,
            ctx=SimpleNamespace(actor_ip=None, request_id=None),  # type: ignore[arg-type]
            principal=SimpleNamespace(user_id=uuid.uuid4()),  # type: ignore[arg-type]
            db=MagicMock(),
        )
    assert out.read_member_ids == [b]
    assert facade.set_read_members.await_args.kwargs["member_ids"] == [b]


@pytest.mark.parametrize("body", [{"member_ids": []}, {}])
def test_the_body_refuses_an_empty_list_and_a_missing_field(body: dict[str, Any]) -> None:
    from pydantic import ValidationError

    from app.api.v1.agent_groups import ReadMembersIn

    with pytest.raises(ValidationError):
        ReadMembersIn.model_validate(body)
