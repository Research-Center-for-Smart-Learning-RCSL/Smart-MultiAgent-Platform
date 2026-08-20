"""R15.24's dual track on the id-addressed orchestration reads.

Dossier `docs/tasks/2026-08-20-orchestration-room-scoped-reads/spec.md`,
AC-1..AC-5, AC-7, AC-9.

These are route tests in the sense of `test_chatroom_approvals_read.py` — the
handler is called directly, the services are fakes — but the *gate* is real:
only `resolve_room_access` is stubbed, so the flag tiers, the moderator
predicate and the room/backstage branch all execute. Stubbing
`can_read_orchestration_record` instead would leave the actual decision
untested, and the decision is the whole change.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.v1 import orchestration
from app.api.v1.deps import PaginationParams
from contexts.conversation.application import access as access_mod
from contexts.conversation.application.access import RoomAccess
from contexts.conversation.domain.errors import ChatroomNotFound
from contexts.conversation.domain.models import Chatroom
from contexts.orchestration.domain.models import (
    AgentInstance,
    Approval,
    ApprovalMode,
    ApprovalState,
    Instruction,
    InstructionState,
)
from shared_kernel.auth.permissions import Principal, Role, Scope

_PROJECT = uuid.uuid4()
_RUN = uuid.uuid4()
_AGENT = uuid.uuid4()
_NOW = datetime(2026, 8, 20, tzinfo=UTC)

# Two rooms with different tiers, so "readable" and "unreadable" in one test are
# a property of the room rather than of the stub.
_OPEN_ROOM = uuid.uuid4()
_OWNERS_ROOM = uuid.uuid4()


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


def _principal(*, is_admin: bool = False) -> Principal:
    return Principal(user_id=uuid.uuid4(), is_admin=is_admin, email_verified=True)


class _Resolver:
    """Project-scoped role resolver. `roles` is what the caller holds."""

    def __init__(self, roles: frozenset[Role]) -> None:
        self.roles = roles
        self.calls: list[Scope] = []

    async def roles_for(self, principal: Principal, scope: Scope) -> frozenset[Role]:
        self.calls.append(scope)
        return self.roles


def _chatroom(room_id: uuid.UUID, *, owners_only: bool) -> Chatroom:
    return Chatroom(
        id=room_id,
        workspace_id=uuid.uuid4(),
        name="room",
        allow_org_members=False,
        allow_project_members=not owners_only,
        allow_project_owners_only=owners_only,
        allow_guest_links=False,
        guest_token="t",
        version=1,
        created_at=_NOW,
        deleted_at=None,
    )


def _install_rooms(monkeypatch: pytest.MonkeyPatch, roles: frozenset[Role]) -> list[uuid.UUID]:
    """Stub `resolve_room_access` over the two fixture rooms.

    Returns the list of room ids it was asked about, so a test can assert the
    per-room memoisation actually memoises.
    """
    asked: list[uuid.UUID] = []

    async def _fake(_db: Any, *, principal: Principal, chatroom_id: uuid.UUID) -> RoomAccess:
        asked.append(chatroom_id)
        if chatroom_id not in (_OPEN_ROOM, _OWNERS_ROOM):
            raise ChatroomNotFound(str(chatroom_id))
        return RoomAccess(
            chatroom=_chatroom(chatroom_id, owners_only=chatroom_id == _OWNERS_ROOM),
            project_id=_PROJECT,
            roles=roles,
            is_guest=False,
        )

    monkeypatch.setattr(access_mod, "resolve_room_access", _fake)
    return asked


def _approval(*, chatroom_id: uuid.UUID | None) -> Approval:
    return Approval(
        id=uuid.uuid4(),
        workflow_run_id=_RUN,
        mode=ApprovalMode.SINGLE,
        leader_agent_id=_AGENT,
        approver_agent_ids=(_AGENT,),
        timeout_seconds=300,
        state=ApprovalState.PENDING,
        started_at=_NOW,
        ended_at=None,
        chatroom_id=chatroom_id,
    )


def _instance(*, chatroom_id: uuid.UUID | None, parent_id: uuid.UUID | None = None) -> AgentInstance:
    return AgentInstance(
        id=uuid.uuid4(),
        agent_id=_AGENT,
        parent_id=parent_id,
        chatroom_id=chatroom_id,
        run_context={},
        task_description=None,
        state="running",
        spawned_at=_NOW,
        destroyed_at=None,
    )


def _instruction() -> Instruction:
    return Instruction(
        id=uuid.uuid4(),
        chain_id=uuid.uuid4(),
        path=(_AGENT,),
        depth=0,
        issuer_agent_id=_AGENT,
        target_agent_id=_AGENT,
        payload={},
        state=InstructionState.ISSUED,
        issued_at=_NOW,
        resolved_at=None,
    )


def _approval_service(monkeypatch: pytest.MonkeyPatch, approvals: list[Approval]) -> MagicMock:
    svc = MagicMock()
    svc.resolve_project = AsyncMock(return_value=_PROJECT)
    svc.resolve_run_project = AsyncMock(return_value=_PROJECT)
    svc.get_approval = AsyncMock(return_value=approvals[0] if approvals else None)
    svc.get_votes = AsyncMock(return_value=[])
    svc.list_for_run = AsyncMock(return_value=approvals)
    monkeypatch.setattr(orchestration, "ApprovalService", lambda _db: svc)
    return svc


def _page() -> PaginationParams:
    return PaginationParams(limit=100, offset=0)


# ---------------------------------------------------------------------------
# AC-1 — a room-bound approval follows the room ACL
# ---------------------------------------------------------------------------


async def test_room_member_reads_an_approval_bound_to_that_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _approval(chatroom_id=_OPEN_ROOM)
    _approval_service(monkeypatch, [approval])
    _install_rooms(monkeypatch, frozenset({Role.PROJECT_MEMBER}))

    out = await orchestration.get_approval(
        approval_id=approval.id,
        db=MagicMock(),
        principal=_principal(),
        resolver=_Resolver(frozenset({Role.PROJECT_MEMBER})),
    )

    assert out.id == str(approval.id)


async def test_project_member_outside_an_owners_only_room_gets_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The live hole this dossier closes (Q-4): today this returns 200.
    approval = _approval(chatroom_id=_OWNERS_ROOM)
    _approval_service(monkeypatch, [approval])
    _install_rooms(monkeypatch, frozenset({Role.PROJECT_MEMBER}))

    with pytest.raises(HTTPException) as exc:
        await orchestration.get_approval(
            approval_id=approval.id,
            db=MagicMock(),
            principal=_principal(),
            resolver=_Resolver(frozenset({Role.PROJECT_MEMBER})),
        )

    assert exc.value.status_code == 404
    # SEC: byte-identical to a record that does not exist, and naming no room.
    assert exc.value.detail == "approval not found"


async def test_owner_reads_an_owners_only_rooms_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    # OQ-1: moderators clear every room tier, so "room ACL" and "backstage"
    # agree here for different reasons.
    approval = _approval(chatroom_id=_OWNERS_ROOM)
    _approval_service(monkeypatch, [approval])
    _install_rooms(monkeypatch, frozenset({Role.PROJECT_OWNER}))

    out = await orchestration.get_approval(
        approval_id=approval.id,
        db=MagicMock(),
        principal=_principal(),
        resolver=_Resolver(frozenset({Role.PROJECT_OWNER})),
    )

    assert out.id == str(approval.id)


async def test_a_room_that_no_longer_resolves_denies_rather_than_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # §8 fail-closed: ChatroomNotFound must not drop through to the project check.
    approval = _approval(chatroom_id=uuid.uuid4())
    _approval_service(monkeypatch, [approval])
    _install_rooms(monkeypatch, frozenset({Role.PROJECT_OWNER}))

    with pytest.raises(HTTPException) as exc:
        await orchestration.get_approval(
            approval_id=approval.id,
            db=MagicMock(),
            principal=_principal(),
            resolver=_Resolver(frozenset({Role.PROJECT_OWNER})),
        )

    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# AC-2 / AC-5 — a room-less approval is backstage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("roles", "allowed"),
    [
        (frozenset({Role.PROJECT_MEMBER}), False),
        (frozenset({Role.ORG_MEMBER}), False),
        (frozenset(), False),
        (frozenset({Role.PROJECT_OWNER}), True),
        (frozenset({Role.ORG_OWNER}), True),
    ],
)
async def test_roomless_approval_is_owner_only(
    monkeypatch: pytest.MonkeyPatch,
    roles: frozenset[Role],
    allowed: bool,
) -> None:
    # AC-5: this is also the shape a deleted room produces — the FK is
    # ON DELETE SET NULL, so the record arrives here naming no room.
    approval = _approval(chatroom_id=None)
    _approval_service(monkeypatch, [approval])
    _install_rooms(monkeypatch, roles)

    async def _call() -> Any:
        return await orchestration.get_approval(
            approval_id=approval.id,
            db=MagicMock(),
            principal=_principal(),
            resolver=_Resolver(roles),
        )

    if allowed:
        assert (await _call()).id == str(approval.id)
    else:
        with pytest.raises(HTTPException) as exc:
            await _call()
        # Backstage denial has no room to hide, so it says so rather than 404ing.
        assert exc.value.status_code == 403


async def test_admin_reads_a_roomless_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    approval = _approval(chatroom_id=None)
    _approval_service(monkeypatch, [approval])
    resolver = _Resolver(frozenset())

    out = await orchestration.get_approval(
        approval_id=approval.id,
        db=MagicMock(),
        principal=_principal(is_admin=True),
        resolver=resolver,
    )

    assert out.id == str(approval.id)
    assert resolver.calls == []  # admin short-circuits before any role lookup


# ---------------------------------------------------------------------------
# AC-3 — the run's approval list omits, it does not refuse
# ---------------------------------------------------------------------------


async def test_run_approvals_omit_unreadable_rows_with_200(monkeypatch: pytest.MonkeyPatch) -> None:
    readable = _approval(chatroom_id=_OPEN_ROOM)
    hidden_room = _approval(chatroom_id=_OWNERS_ROOM)
    hidden_backstage = _approval(chatroom_id=None)
    _approval_service(monkeypatch, [readable, hidden_room, hidden_backstage])
    _install_rooms(monkeypatch, frozenset({Role.PROJECT_MEMBER}))

    out = await orchestration.list_approvals_for_run(
        workflow_run_id=_RUN,
        pagination=_page(),
        db=MagicMock(),
        principal=_principal(),
        resolver=_Resolver(frozenset({Role.PROJECT_MEMBER})),
    )

    assert [a.id for a in out] == [str(readable.id)]


async def test_run_approvals_are_empty_for_a_non_member(monkeypatch: pytest.MonkeyPatch) -> None:
    # Q-1 decision: the list routes have no project-level precondition; every
    # row is gated individually, so an outsider sees [] rather than a 403.
    _approval_service(monkeypatch, [_approval(chatroom_id=_OPEN_ROOM), _approval(chatroom_id=None)])
    _install_rooms(monkeypatch, frozenset())

    out = await orchestration.list_approvals_for_run(
        workflow_run_id=_RUN,
        pagination=_page(),
        db=MagicMock(),
        principal=_principal(),
        resolver=_Resolver(frozenset()),
    )

    assert out == []


async def test_filtering_happens_before_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    # SEC: slicing first would make the page length report how many rows were
    # withheld. Two readable rows behind two unreadable ones must still fill a
    # page of two.
    hidden_a = _approval(chatroom_id=_OWNERS_ROOM)
    hidden_b = _approval(chatroom_id=_OWNERS_ROOM)
    readable_a = _approval(chatroom_id=_OPEN_ROOM)
    readable_b = _approval(chatroom_id=_OPEN_ROOM)
    _approval_service(monkeypatch, [hidden_a, hidden_b, readable_a, readable_b])
    _install_rooms(monkeypatch, frozenset({Role.PROJECT_MEMBER}))

    out = await orchestration.list_approvals_for_run(
        workflow_run_id=_RUN,
        pagination=PaginationParams(limit=2, offset=0),
        db=MagicMock(),
        principal=_principal(),
        resolver=_Resolver(frozenset({Role.PROJECT_MEMBER})),
    )

    assert [a.id for a in out] == [str(readable_a.id), str(readable_b.id)]


async def test_room_verdicts_are_resolved_once_per_room(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_approval(chatroom_id=_OPEN_ROOM) for _ in range(5)]
    _approval_service(monkeypatch, rows)
    asked = _install_rooms(monkeypatch, frozenset({Role.PROJECT_MEMBER}))

    await orchestration.list_approvals_for_run(
        workflow_run_id=_RUN,
        pagination=_page(),
        db=MagicMock(),
        principal=_principal(),
        resolver=_Resolver(frozenset({Role.PROJECT_MEMBER})),
    )

    assert asked == [_OPEN_ROOM]


# ---------------------------------------------------------------------------
# AC-4 — agent instances take the same track
# ---------------------------------------------------------------------------


async def test_run_subagents_are_filtered_by_room(monkeypatch: pytest.MonkeyPatch) -> None:
    readable = _instance(chatroom_id=_OPEN_ROOM)
    hidden = _instance(chatroom_id=_OWNERS_ROOM)
    facade = MagicMock()
    facade.resolve_workflow_run_project = AsyncMock(return_value=_PROJECT)
    facade.list_workflow_run_subagents = AsyncMock(return_value=[readable, hidden])
    monkeypatch.setattr(orchestration, "OrchestrationFacade", lambda _db: facade)
    _install_rooms(monkeypatch, frozenset({Role.PROJECT_MEMBER}))

    out = await orchestration.list_run_subagents(
        workflow_run_id=_RUN,
        pagination=_page(),
        db=MagicMock(),
        principal=_principal(),
        resolver=_Resolver(frozenset({Role.PROJECT_MEMBER})),
    )

    assert [i.id for i in out] == [str(readable.id)]


async def test_children_of_an_unreadable_parent_are_404(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _instance(chatroom_id=_OWNERS_ROOM)
    svc = MagicMock()
    svc.resolve_project = AsyncMock(return_value=_PROJECT)
    svc.get_instance = AsyncMock(return_value=parent)
    svc.list_children = AsyncMock(return_value=[])
    monkeypatch.setattr(orchestration, "SubagentService", lambda _db: svc)
    _install_rooms(monkeypatch, frozenset({Role.PROJECT_MEMBER}))

    with pytest.raises(HTTPException) as exc:
        await orchestration.list_subagent_children(
            parent_instance_id=parent.id,
            pagination=_page(),
            db=MagicMock(),
            principal=_principal(),
            resolver=_Resolver(frozenset({Role.PROJECT_MEMBER})),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "agent instance not found"
    svc.list_children.assert_not_awaited()


async def test_children_in_another_room_than_the_parent_are_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _instance(chatroom_id=_OPEN_ROOM)
    same_room = _instance(chatroom_id=_OPEN_ROOM, parent_id=parent.id)
    elsewhere = _instance(chatroom_id=_OWNERS_ROOM, parent_id=parent.id)
    svc = MagicMock()
    svc.resolve_project = AsyncMock(return_value=_PROJECT)
    svc.get_instance = AsyncMock(return_value=parent)
    svc.list_children = AsyncMock(return_value=[same_room, elsewhere])
    monkeypatch.setattr(orchestration, "SubagentService", lambda _db: svc)
    _install_rooms(monkeypatch, frozenset({Role.PROJECT_MEMBER}))

    out = await orchestration.list_subagent_children(
        parent_instance_id=parent.id,
        pagination=_page(),
        db=MagicMock(),
        principal=_principal(),
        resolver=_Resolver(frozenset({Role.PROJECT_MEMBER})),
    )

    assert [i.id for i in out] == [str(same_room.id)]


# ---------------------------------------------------------------------------
# AC-7 — instructions carry no room, so they are backstage
# ---------------------------------------------------------------------------


def _instruct_service(monkeypatch: pytest.MonkeyPatch, instruction: Instruction) -> MagicMock:
    svc = MagicMock()
    svc.resolve_instruction_project = AsyncMock(return_value=_PROJECT)
    svc.resolve_chain_project = AsyncMock(return_value=_PROJECT)
    svc.get_instruction = AsyncMock(return_value=instruction)
    svc.list_for_chain = AsyncMock(return_value=[instruction])
    monkeypatch.setattr(orchestration, "InstructService", lambda _db: svc)
    return svc


async def test_instruction_read_requires_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = _instruction()
    _instruct_service(monkeypatch, instruction)

    with pytest.raises(HTTPException) as exc:
        await orchestration.get_instruction(
            instruction_id=instruction.id,
            db=MagicMock(),
            principal=_principal(),
            resolver=_Resolver(frozenset({Role.PROJECT_MEMBER})),
        )

    assert exc.value.status_code == 403


async def test_owner_reads_an_instruction_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = _instruction()
    _instruct_service(monkeypatch, instruction)

    out = await orchestration.list_instructions_for_chain(
        chain_id=instruction.chain_id,
        pagination=_page(),
        db=MagicMock(),
        principal=_principal(),
        resolver=_Resolver(frozenset({Role.PROJECT_OWNER})),
    )

    assert [i.id for i in out] == [str(instruction.id)]


async def test_chain_read_requires_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = _instruction()
    _instruct_service(monkeypatch, instruction)

    with pytest.raises(HTTPException) as exc:
        await orchestration.list_instructions_for_chain(
            chain_id=instruction.chain_id,
            pagination=_page(),
            db=MagicMock(),
            principal=_principal(),
            resolver=_Resolver(frozenset({Role.PROJECT_MEMBER})),
        )

    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# AC-9 — the weak gate survives for exactly one route
# ---------------------------------------------------------------------------


def test_assert_project_member_has_only_the_dlq_caller() -> None:
    from pathlib import Path

    source = Path(orchestration.__file__).read_text(encoding="utf-8")
    call_sites = source.count("_assert_project_member(")
    # One definition, one call. Any third occurrence means a route reached for
    # bare project membership again.
    assert call_sites == 2, "bare project membership is the DLQ route's gate only (FU-1)"


async def test_dlq_still_admits_a_plain_project_member(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = SimpleNamespace(project_id=_PROJECT)
    facade = MagicMock()
    facade.get_agent = AsyncMock(return_value=agent)
    monkeypatch.setattr(orchestration, "AgentsFacade", lambda _db: facade)
    monkeypatch.setattr(orchestration, "read_dlq", AsyncMock(return_value=[]))

    out = await orchestration.get_agent_dlq(
        agent_id=_AGENT,
        db=MagicMock(),
        principal=_principal(),
        resolver=_Resolver(frozenset({Role.PROJECT_MEMBER})),
    )

    assert out == []
