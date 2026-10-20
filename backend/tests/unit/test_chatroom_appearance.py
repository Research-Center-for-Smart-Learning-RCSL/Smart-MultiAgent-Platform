"""Room appearance authority, disclosure and audit semantics ([R13.78])."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.api.v1.chatrooms as routes
from contexts.conversation.application.access import RoomAccess
from contexts.conversation.application.chatroom_service import ChatroomService
from contexts.conversation.domain.models import ChatroomAgent, ChatroomAgentBubbleColor, ChatroomAgentRole
from shared_kernel.auth.permissions import Principal, Role
from tests.unit.chatroom_fakes import chatroom_row


@pytest.mark.parametrize(
    ("role", "creator", "admin", "guest", "allowed"),
    [
        (Role.PROJECT_MEMBER, True, False, False, True),
        (Role.PROJECT_OWNER, False, False, False, True),
        (Role.ORG_OWNER, False, False, False, True),
        (None, False, True, False, True),
        (Role.PROJECT_MEMBER, False, False, False, False),
        (Role.ORG_MEMBER, False, False, False, False),
        (None, False, False, True, False),
    ],
)
async def test_appearance_audience(monkeypatch, role, creator, admin, guest, allowed):
    principal = Principal(user_id=uuid.uuid4(), is_admin=admin, email_verified=True)
    room = chatroom_row(created_by=principal.user_id if creator else uuid.uuid4())
    room.allow_org_members = True
    access = RoomAccess(
        chatroom=room,
        project_id=uuid.uuid4(),
        roles=frozenset({role}) if role else frozenset(),
        is_guest=guest,
    )
    facade = SimpleNamespace(
        agent_role_in_chatroom=AsyncMock(return_value=ChatroomAgentRole.NORMAL),
        set_agent_bubble_color=AsyncMock(return_value="written"),
    )
    monkeypatch.setattr(routes, "resolve_room_access", AsyncMock(return_value=access))
    monkeypatch.setattr(routes, "ConversationFacade", lambda db: facade)
    emit = AsyncMock()
    monkeypatch.setattr(routes, "_emit_chatroom_updated", emit)
    call = routes.patch_chatroom_agent_appearance(
        body=routes.AgentAppearanceIn(bubble_color="teal"),
        chatroom_id=room.id,
        agent_id=uuid.uuid4(),
        principal=principal,
        db=SimpleNamespace(),
        ctx=SimpleNamespace(actor_ip=None, request_id=None),
    )
    if allowed:
        await call
        assert facade.set_agent_bubble_color.await_args.kwargs["color"] is ChatroomAgentBubbleColor.TEAL
        assert emit.await_args.kwargs["room_visible"] is True
    else:
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 403
        facade.set_agent_bubble_color.assert_not_awaited()
        emit.assert_not_awaited()


@pytest.mark.parametrize(("result", "status"), [("not_bound", 404), ("observer", 409), ("unchanged", None)])
async def test_appearance_outcomes(monkeypatch, result, status):
    uid = uuid.uuid4()
    principal = Principal(user_id=uid, is_admin=False, email_verified=True)
    access = RoomAccess(
        chatroom=chatroom_row(created_by=uid),
        project_id=uuid.uuid4(),
        roles=frozenset({Role.PROJECT_MEMBER}),
        is_guest=False,
    )
    facade = SimpleNamespace(
        agent_role_in_chatroom=AsyncMock(return_value=ChatroomAgentRole.NORMAL),
        set_agent_bubble_color=AsyncMock(return_value=result),
    )
    monkeypatch.setattr(routes, "resolve_room_access", AsyncMock(return_value=access))
    monkeypatch.setattr(routes, "ConversationFacade", lambda db: facade)
    emit = AsyncMock()
    monkeypatch.setattr(routes, "_emit_chatroom_updated", emit)
    call = routes.patch_chatroom_agent_appearance(
        body=routes.AgentAppearanceIn(bubble_color=None),
        chatroom_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        principal=principal,
        db=SimpleNamespace(),
        ctx=SimpleNamespace(actor_ip=None, request_id=None),
    )
    if status:
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == status
    else:
        await call
    emit.assert_not_awaited()


def test_closed_palette_and_required_field():
    for value in ("blue", "red; color: red", "", 123):
        with pytest.raises(ValidationError):
            routes.AgentAppearanceIn(bubble_color=value)
    with pytest.raises(ValidationError):
        routes.AgentAppearanceIn.model_validate({})


@pytest.mark.parametrize("result", ["observer", "not_bound"])
async def test_hidden_observer_is_indistinguishable_from_unbound_for_an_editor(monkeypatch, result):
    uid = uuid.uuid4()
    access = RoomAccess(
        chatroom=chatroom_row(created_by=uuid.uuid4()),
        project_id=uuid.uuid4(),
        roles=frozenset({Role.PROJECT_OWNER}),
        is_guest=False,
    )
    facade = SimpleNamespace(
        agent_role_in_chatroom=AsyncMock(return_value=ChatroomAgentRole.OBSERVER),
        set_agent_bubble_color=AsyncMock(return_value=result),
    )
    monkeypatch.setattr(routes, "resolve_room_access", AsyncMock(return_value=access))
    monkeypatch.setattr(routes, "ConversationFacade", lambda db: facade)
    with pytest.raises(HTTPException) as exc:
        await routes.patch_chatroom_agent_appearance(
            body=routes.AgentAppearanceIn(bubble_color="teal"),
            chatroom_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            principal=Principal(user_id=uid, is_admin=False, email_verified=True),
            db=SimpleNamespace(),
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "agent is not bound to this chatroom"
    facade.agent_role_in_chatroom.assert_not_awaited()


@pytest.mark.parametrize(
    ("appearance", "color", "result", "audited"),
    [
        (None, None, "not_bound", False),
        ((ChatroomAgentRole.OBSERVER, None), None, "observer", False),
        ((ChatroomAgentRole.NORMAL, None), None, "unchanged", False),
        (
            (ChatroomAgentRole.NORMAL, ChatroomAgentBubbleColor.TEAL),
            ChatroomAgentBubbleColor.TEAL,
            "unchanged",
            False,
        ),
        ((ChatroomAgentRole.NORMAL, None), ChatroomAgentBubbleColor.TEAL, "written", True),
        ((ChatroomAgentRole.NORMAL, ChatroomAgentBubbleColor.TEAL), None, "written", True),
    ],
)
async def test_service_audits_only_effective_changes(monkeypatch, appearance, color, result, audited):
    from contexts.conversation.application import chatroom_service

    service = ChatroomService(SimpleNamespace())
    service._agents = SimpleNamespace(
        locked_appearance=AsyncMock(return_value=appearance), set_bubble_color=AsyncMock(return_value=True)
    )
    emit = AsyncMock()
    monkeypatch.setattr(chatroom_service.audit, "emit", emit)
    agent_id = uuid.uuid4()
    assert (
        await service.set_agent_bubble_color(
            chatroom_id=uuid.uuid4(),
            agent_id=agent_id,
            color=color,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
        == result
    )
    assert emit.await_count == int(audited)
    assert service._agents.set_bubble_color.await_count == int(audited)
    if audited:
        event = emit.await_args.args[1]
        assert event.action == "chatroom.agent_appearance_changed"
        assert event.metadata == {
            "agent_id": str(agent_id),
            "old_color": appearance[1],
            "new_color": color,
        }


@pytest.mark.parametrize("creator", [False, True])
@pytest.mark.parametrize("guest", [False, True])
async def test_listing_exposes_color_without_disclosing_observers(monkeypatch, creator, guest):
    uid, agent_id, observer_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    room = chatroom_row(created_by=uid if creator else uuid.uuid4())
    access = RoomAccess(
        chatroom=room,
        project_id=uuid.uuid4(),
        roles=frozenset() if guest else frozenset({Role.PROJECT_MEMBER}),
        is_guest=guest,
    )
    monkeypatch.setattr(routes, "resolve_room_access", AsyncMock(return_value=access))
    monkeypatch.setattr(
        routes,
        "ChatroomService",
        lambda db: SimpleNamespace(
            list_agents=AsyncMock(
                return_value=[
                    ChatroomAgent(
                        chatroom_id=room.id, agent_id=agent_id, bubble_color=ChatroomAgentBubbleColor.ROSE
                    ),
                    ChatroomAgent(chatroom_id=room.id, agent_id=observer_id, role=ChatroomAgentRole.OBSERVER),
                ]
            )
        ),
    )
    monkeypatch.setattr(
        routes, "AgentsFacade", lambda db: SimpleNamespace(agent_names=AsyncMock(return_value={}))
    )
    out = await routes.list_chatroom_agents(
        chatroom_id=room.id,
        pagination=SimpleNamespace(offset=0, limit=100),
        principal=Principal(user_id=uid, is_admin=False, email_verified=True),
        db=SimpleNamespace(),
    )
    assert out[0].bubble_color is ChatroomAgentBubbleColor.ROSE
    assert [r.agent_id for r in out] == ([agent_id, observer_id] if creator and not guest else [agent_id])
