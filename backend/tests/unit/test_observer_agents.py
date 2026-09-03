"""SRS §28 Observer Agents — creator access rule, release semantics, and the
turn-engine leak pin.

The load-bearing guarantee (R28.01/R28.03) is structural: observer output
never becomes a `messages` row and never touches the room WS channel. The
turn-engine test here spies every Publisher construction and asserts zero
room-channel traffic across a full observer turn.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

import contexts.agents.application.runtime.turn_engine as te
import contexts.conversation.application.observation_service as obs_svc
from contexts.conversation.application.access import (
    RoomAccess,
    ensure_room_creator,
    is_room_creator,
)
from contexts.conversation.domain.errors import (
    InvalidReleaseTarget,
    NotRoomCreator,
    ObservationAlreadyReleased,
    ObservationNotFound,
)
from contexts.conversation.domain.models import (
    AgentObservation,
    ChatroomAgentRole,
    SenderType,
)
from contexts.conversation.infrastructure.repositories.observation_repo import ObservationRepository
from contexts.skills.application.binding_service import BoundSet
from shared_kernel.auth.permissions import Principal, Role
from tests.unit.chatroom_fakes import chatroom_row
from tests.unit.skill_fakes import make_skill

# --------------------------------------------------------------------------- #
# is_room_creator / ensure_room_creator (R28.02)
# --------------------------------------------------------------------------- #


def _principal(user_id=None, *, is_admin=False):
    return Principal(user_id=user_id or uuid.uuid4(), is_admin=is_admin, email_verified=True)


def _access(
    *,
    created_by=None,
    roles=frozenset(),
    is_guest=False,
    disclose_observers=True,
    disclose_drafts=True,
):
    # Both disclosure flags default to True, matching the column default
    # (`0041_observer_agents.py`) and the domain model — so a test that does not
    # mention them describes a disclosing room, which is the common case.
    room = SimpleNamespace(
        created_by_user_id=created_by,
        disclose_observers=disclose_observers,
        disclose_drafts=disclose_drafts,
    )
    return RoomAccess(chatroom=room, project_id=uuid.uuid4(), roles=roles, is_guest=is_guest)


def test_creator_match_allows() -> None:
    uid = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_MEMBER}))
    assert is_room_creator(access, principal=_principal(uid)) is True


def test_creator_removed_from_project_denied() -> None:
    """O-7 (R28.02): creator authority requires current membership — a user
    removed from the project keeps the created_by match but loses all roles."""
    uid = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset(), is_guest=False)
    assert is_room_creator(access, principal=_principal(uid)) is False


def test_non_creator_member_denied() -> None:
    access = _access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_MEMBER}))
    assert is_room_creator(access, principal=_principal()) is False


def test_moderator_denied_when_creator_exists() -> None:
    # A live creator makes observer surfaces exclusive to them — a project
    # owner does NOT inherit access.
    access = _access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER}))
    assert is_room_creator(access, principal=_principal()) is False


def test_null_creator_falls_back_to_moderator() -> None:
    access = _access(created_by=None, roles=frozenset({Role.PROJECT_OWNER}))
    assert is_room_creator(access, principal=_principal()) is True


def test_null_creator_plain_member_denied() -> None:
    access = _access(created_by=None, roles=frozenset({Role.PROJECT_MEMBER}))
    assert is_room_creator(access, principal=_principal()) is False


def test_admin_bypasses() -> None:
    access = _access(created_by=uuid.uuid4())
    assert is_room_creator(access, principal=_principal(is_admin=True)) is True


def test_pure_guest_always_denied() -> None:
    # Guests satisfy ensure_can_read when guest links are on — the explicit
    # guest branch is what keeps them out of observer surfaces.
    access = _access(created_by=None, roles=frozenset(), is_guest=True)
    assert is_room_creator(access, principal=_principal()) is False


def test_guest_who_is_also_creator_allowed() -> None:
    # A registered user can be both a guest row and the creator (edge case);
    # roles being non-empty routes past the pure-guest branch.
    uid = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_MEMBER}), is_guest=True)
    assert is_room_creator(access, principal=_principal(uid)) is True


def test_ensure_room_creator_raises() -> None:
    with pytest.raises(NotRoomCreator):
        ensure_room_creator(_access(created_by=uuid.uuid4()), principal=_principal())


# --------------------------------------------------------------------------- #
# Chatroom handler gates — unbind (O-5), disclosure-only patch (O-6),
# guest-neutral DTO (O-8)
# --------------------------------------------------------------------------- #


def test_to_out_hides_observer_fields_from_pure_guests() -> None:
    """O-8 (R28.02): a pure guest gets fail-closed neutral values for every
    observer-related DTO field; members keep the real values."""
    import app.api.v1.chatrooms as chatrooms_mod

    creator = uuid.uuid4()
    room = chatroom_row(created_by=creator, disclose=True)

    guest_view = chatrooms_mod._to_out(room, has_observers=True, viewer_is_pure_guest=True)
    assert guest_view.created_by_user_id is None
    assert guest_view.disclose_observers is False
    assert guest_view.observers_present is False

    member_view = chatrooms_mod._to_out(room, has_observers=True)
    assert member_view.created_by_user_id == creator
    assert member_view.disclose_observers is True
    assert member_view.observers_present is True


_CTX = SimpleNamespace(actor_ip=None, request_id=None)


# --------------------------------------------------------------------------- #
# Delegated activity control ([R30.37]) — the grant route and its listing
# --------------------------------------------------------------------------- #


def _bound_agent(agent_id, *, role=None, granted=False, allowlist=()):
    from contexts.conversation.domain.models import ChatroomAgent

    return ChatroomAgent(
        chatroom_id=uuid.uuid4(),
        agent_id=agent_id,
        role=role or ChatroomAgentRole.NORMAL,
        may_control_activities=granted,
        activity_type_allowlist=tuple(allowlist),
    )


def _wire_grant_route(monkeypatch, *, access, written=True, resolves=True):
    """Stub everything the grant route reaches except the decision under test.

    The publisher spy is installed here rather than per-test on purpose. The route
    emits, and `_emit_chatroom_updated` swallows transport failure by design — so
    without the spy every test below would construct the real `Publisher`, fail to
    reach Redis, have the failure logged and discarded, and pass while asserting
    nothing about what was emitted. A frame assertion that cannot fail is worse
    than no frame assertion.
    """
    import app.api.v1.chatrooms as chatrooms_mod
    from contexts.activities.domain.errors import ActivityTypeNotFound

    calls: dict[str, list] = {"grant": [], "resolved": []}

    async def _resolve(db, *, principal, chatroom_id):
        return access

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def set_agent_activity_grant(self, **kw):
            calls["grant"].append(kw)
            return written

        async def resolve_type_for_project(self, *, project_id, activity_type_id):
            calls["resolved"].append(activity_type_id)
            if not resolves:
                raise ActivityTypeNotFound(str(activity_type_id))
            return object()

    monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(chatrooms_mod, "ConversationFacade", _Facade)
    monkeypatch.setattr(
        "contexts.activities.interfaces.facade.ActivitiesFacade",
        _Facade,
    )
    _spy_room_publisher(monkeypatch, chatrooms_mod)
    return chatrooms_mod, calls


@pytest.mark.asyncio
async def test_creator_may_grant_activity_control(monkeypatch) -> None:
    """AC-1: the room creator writes the grant, and every named type is resolved
    for the room's own project *before* anything is stored."""
    uid = uuid.uuid4()
    type_ids = [uuid.uuid4(), uuid.uuid4()]
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    mod, calls = _wire_grant_route(monkeypatch, access=access)
    db = SimpleNamespace(commit=AsyncMock())

    await mod.patch_chatroom_agent_activity_control(
        body=mod.AgentActivityControlIn(granted=True, activity_type_ids=type_ids),
        chatroom_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        ctx=_CTX,
        principal=_principal(uid),
        db=db,
    )

    assert calls["resolved"] == type_ids
    assert calls["grant"][0]["granted"] is True
    assert calls["grant"][0]["activity_type_ids"] == type_ids
    assert calls["grant"][0]["actor_user_id"] == uid
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_non_creator_cannot_write_the_grant(monkeypatch) -> None:
    """AC-2's write half: a project owner who is not the creator is refused, and
    nothing is resolved or written before the refusal."""
    access = _access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER}))
    mod, calls = _wire_grant_route(monkeypatch, access=access)
    db = SimpleNamespace(commit=AsyncMock())

    with pytest.raises(NotRoomCreator):
        await mod.patch_chatroom_agent_activity_control(
            body=mod.AgentActivityControlIn(granted=True, activity_type_ids=[uuid.uuid4()]),
            chatroom_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            ctx=_CTX,
            principal=_principal(),
            db=db,
        )

    assert calls == {"grant": [], "resolved": []}
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_guest_cannot_write_the_grant(monkeypatch) -> None:
    """AC-2, the pure-guest arm: `is_room_creator` excludes guests explicitly."""
    access = _access(created_by=None, roles=frozenset(), is_guest=True)
    mod, _calls = _wire_grant_route(monkeypatch, access=access)

    with pytest.raises(NotRoomCreator):
        await mod.patch_chatroom_agent_activity_control(
            body=mod.AgentActivityControlIn(granted=False),
            chatroom_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            ctx=_CTX,
            principal=_principal(),
            db=SimpleNamespace(commit=AsyncMock()),
        )


@pytest.mark.asyncio
async def test_granting_with_an_empty_allowlist_is_refused(monkeypatch) -> None:
    """AC-3's route half — the same state ck_chatroom_agents_activity_grant
    refuses in the DB. Authority over nothing still reads as authority."""
    uid = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    mod, calls = _wire_grant_route(monkeypatch, access=access)

    with pytest.raises(HTTPException) as exc:
        await mod.patch_chatroom_agent_activity_control(
            body=mod.AgentActivityControlIn(granted=True, activity_type_ids=[]),
            chatroom_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            ctx=_CTX,
            principal=_principal(uid),
            db=SimpleNamespace(commit=AsyncMock()),
        )

    assert exc.value.status_code == 422
    assert calls["grant"] == []


@pytest.mark.asyncio
async def test_an_unreachable_type_id_is_refused_before_any_write(monkeypatch) -> None:
    """AC-4: another project's type, or a soft-deleted one, is a 422. The four
    reasons a type does not resolve collapse into one message on purpose."""
    uid = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    mod, calls = _wire_grant_route(monkeypatch, access=access, resolves=False)
    db = SimpleNamespace(commit=AsyncMock())

    with pytest.raises(HTTPException) as exc:
        await mod.patch_chatroom_agent_activity_control(
            body=mod.AgentActivityControlIn(granted=True, activity_type_ids=[uuid.uuid4()]),
            chatroom_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            ctx=_CTX,
            principal=_principal(uid),
            db=db,
        )

    assert exc.value.status_code == 422
    assert calls["grant"] == []
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoking_needs_no_type_ids_and_resolves_none(monkeypatch) -> None:
    """AC-13's revoke half. A revoke names no types — the stored allowlist stays
    put so the teacher's selection survives a re-grant — so there is nothing to
    resolve and an empty list must not be refused here."""
    uid = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    mod, calls = _wire_grant_route(monkeypatch, access=access)

    await mod.patch_chatroom_agent_activity_control(
        body=mod.AgentActivityControlIn(granted=False),
        chatroom_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        ctx=_CTX,
        principal=_principal(uid),
        db=SimpleNamespace(commit=AsyncMock()),
    )

    assert calls["resolved"] == []
    assert calls["grant"][0]["granted"] is False


@pytest.mark.asyncio
async def test_activity_control_grant_reaches_the_creator_and_never_the_room(
    monkeypatch,
) -> None:
    """FU-11: the grant was the one binding write that announced nothing.

    Room-visible is `False` here, unlike every other binding write, and the reason
    is not a disclosure flag — there is none for this. `list_chatroom_agents`
    serialises `may_control_activities` and `activity_type_allowlist` as `None` for
    a non-creator under `response_model_exclude_none`, so those fields are dropped
    from the response entirely, and neither appears in `ChatroomOut`. A non-creator
    who received this frame would re-read an unchanged DTO *and* an unchanged agent
    listing, which is exactly the "an invisible write happened" signal the
    room-visible gate exists to withhold.
    """
    uid = uuid.uuid4()
    chatroom_id = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    mod, _calls = _wire_grant_route(monkeypatch, access=access)

    await mod.patch_chatroom_agent_activity_control(
        body=mod.AgentActivityControlIn(granted=True, activity_type_ids=[uuid.uuid4()]),
        chatroom_id=chatroom_id,
        agent_id=uuid.uuid4(),
        ctx=_CTX,
        principal=_principal(uid),
        db=_committing_db(),
    )

    _assert_creator_only(chatroom_id, uid)


@pytest.mark.asyncio
async def test_a_refused_activity_control_grant_emits_nothing(monkeypatch) -> None:
    """The 404 arm must not announce a write that did not happen."""
    uid = uuid.uuid4()
    chatroom_id = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    mod, _calls = _wire_grant_route(monkeypatch, access=access, written=False)

    with pytest.raises(HTTPException) as exc:
        await mod.patch_chatroom_agent_activity_control(
            body=mod.AgentActivityControlIn(granted=False),
            chatroom_id=chatroom_id,
            agent_id=uuid.uuid4(),
            ctx=_CTX,
            principal=_principal(uid),
            db=_committing_db(),
        )

    assert exc.value.status_code == 404
    assert _PublisherSpy.emitted == []


def test_the_allowlist_is_bounded() -> None:
    """Every id costs a reachability query at the route AND another on every turn
    of the granted agent, so an unbounded list is not a one-off cost but a
    permanent per-turn one on an agent that may wake on every message."""
    import pydantic

    import app.api.v1.chatrooms as chatrooms_mod

    over = [uuid.uuid4() for _ in range(chatrooms_mod._MAX_ACTIVITY_ALLOWLIST + 1)]
    with pytest.raises(pydantic.ValidationError):
        chatrooms_mod.AgentActivityControlIn(granted=True, activity_type_ids=over)

    # The ceiling itself is accepted — this bounds abuse, it is not a working limit.
    at_cap = chatrooms_mod.AgentActivityControlIn(
        granted=True, activity_type_ids=over[: chatrooms_mod._MAX_ACTIVITY_ALLOWLIST]
    )
    assert len(at_cap.activity_type_ids) == chatrooms_mod._MAX_ACTIVITY_ALLOWLIST


@pytest.mark.asyncio
async def test_granting_to_an_unbound_agent_is_404(monkeypatch) -> None:
    uid = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    mod, _calls = _wire_grant_route(monkeypatch, access=access, written=False)
    db = SimpleNamespace(commit=AsyncMock())

    with pytest.raises(HTTPException) as exc:
        await mod.patch_chatroom_agent_activity_control(
            body=mod.AgentActivityControlIn(granted=True, activity_type_ids=[uuid.uuid4()]),
            chatroom_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            ctx=_CTX,
            principal=_principal(uid),
            db=db,
        )

    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()


def _wire_agent_listing(monkeypatch, *, access, rows):
    import app.api.v1.chatrooms as chatrooms_mod

    async def _resolve(db, *, principal, chatroom_id):
        return access

    class _Service:
        def __init__(self, db) -> None:
            pass

        async def list_agents(self, chatroom_id):
            return rows

    monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(chatrooms_mod, "ChatroomService", _Service)
    return chatrooms_mod


@pytest.mark.asyncio
async def test_the_listing_shows_the_grant_to_the_creator(monkeypatch) -> None:
    """AC-1's round-trip half."""
    uid, agent_id, type_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    rows = [_bound_agent(agent_id, granted=True, allowlist=(type_id,))]
    mod = _wire_agent_listing(monkeypatch, access=access, rows=rows)

    out = await mod.list_chatroom_agents(
        chatroom_id=uuid.uuid4(),
        pagination=SimpleNamespace(offset=0, limit=50),
        principal=_principal(uid),
        db=object(),
    )

    assert out[0].may_control_activities is True
    assert out[0].activity_type_allowlist == [type_id]


@pytest.mark.asyncio
async def test_the_listing_hides_the_grant_from_a_non_creator(monkeypatch) -> None:
    """AC-2's read half ([R28.10]): a non-creator must not learn the room's
    delegation layout. `None` is dropped by `response_model_exclude_none`, so the
    response stays shape-identical to the pre-grant API."""
    agent_id, type_id = uuid.uuid4(), uuid.uuid4()
    access = _access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_MEMBER}))
    rows = [_bound_agent(agent_id, granted=True, allowlist=(type_id,))]
    mod = _wire_agent_listing(monkeypatch, access=access, rows=rows)

    out = await mod.list_chatroom_agents(
        chatroom_id=uuid.uuid4(),
        pagination=SimpleNamespace(offset=0, limit=50),
        principal=_principal(),
        db=object(),
    )

    assert out[0].may_control_activities is None
    assert out[0].activity_type_allowlist is None
    dumped = out[0].model_dump(exclude_none=True)
    assert "may_control_activities" not in dumped
    assert "activity_type_allowlist" not in dumped


@pytest.mark.asyncio
async def test_remove_agent_restrict_to_normal_scopes_delete(monkeypatch) -> None:
    """O-5: restrict_to_normal maps to a role-scoped repo delete; a no-op
    delete (observer target, rowcount 0) emits no audit."""
    from contexts.conversation.application import chatroom_service as cs

    calls: list = []
    audits: list = []

    class _Repo:
        def __init__(self, db) -> None:
            pass

        async def remove(self, *, chatroom_id, agent_id, only_role=None):
            calls.append(only_role)
            return only_role is None  # observer no-op when scoped to NORMAL

    monkeypatch.setattr(cs, "ChatroomAgentRepository", _Repo)
    monkeypatch.setattr(cs, "ChatroomRepository", lambda db: object())
    monkeypatch.setattr(cs, "ChatroomGuestRepository", lambda db: object())
    monkeypatch.setattr(cs, "WorkspaceRepository", lambda db: object())

    async def _emit(db, event):
        audits.append(event.action)

    monkeypatch.setattr(cs.audit, "emit", _emit)

    service = cs.ChatroomService(object())
    # Non-creator path: scoped to NORMAL, observer target → no delete, no audit.
    await service.remove_agent(
        chatroom_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
        restrict_to_normal=True,
    )
    assert calls[-1] is ChatroomAgentRole.NORMAL
    assert audits == []

    # Creator path: unrestricted, removes and audits.
    await service.remove_agent(
        chatroom_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
    )
    assert calls[-1] is None
    assert audits == ["chatroom.agent_removed"]


def _wire_remove_handler(monkeypatch, *, access, removed):
    import app.api.v1.chatrooms as chatrooms_mod

    async def _pid(db, chatroom_id):
        return uuid.uuid4()

    async def _cap(db, principal, project_id, capability):
        return None

    async def _resolve(db, *, principal, chatroom_id):
        return access

    class _Service:
        def __init__(self, db) -> None:
            pass

        async def remove_agent(self, **kw):
            removed.append(kw)

    monkeypatch.setattr(chatrooms_mod, "_project_id_for_chatroom", _pid)
    monkeypatch.setattr(chatrooms_mod, "_require_project_cap", _cap)
    monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(chatrooms_mod, "ChatroomService", _Service)
    # The handler reads the binding's role to decide whether the unbind is
    # room-visible. Default to NORMAL so cases about the *gate* need not care;
    # `_stub_role` overrides it for the cases that are about the role.
    _stub_role(monkeypatch, chatrooms_mod, ChatroomAgentRole.NORMAL)
    return chatrooms_mod


@pytest.mark.asyncio
async def test_non_creator_unbind_is_scoped_to_normal_no_oracle(monkeypatch) -> None:
    """O-5 (R28.09/R28.10): a non-creator moderator's unbind is restricted to
    normal bindings — never a 403 that would out a hidden observer. The
    role-scoped delete makes an observer target a silent 204 no-op."""
    removed: list = []
    access = _access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER}))
    mod = _wire_remove_handler(monkeypatch, access=access, removed=removed)

    await mod.remove_chatroom_agent(
        chatroom_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        ctx=_CTX,
        principal=_principal(),
        db=_committing_db(),
    )
    assert len(removed) == 1
    assert removed[0]["restrict_to_normal"] is True


@pytest.mark.asyncio
async def test_creator_unbind_is_unrestricted(monkeypatch) -> None:
    """O-5: the creator (or admin) may unbind any binding, observers included."""
    uid = uuid.uuid4()
    removed: list = []
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    mod = _wire_remove_handler(monkeypatch, access=access, removed=removed)

    await mod.remove_chatroom_agent(
        chatroom_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        ctx=_CTX,
        principal=_principal(uid),
        db=_committing_db(),
    )
    assert len(removed) == 1
    assert removed[0]["restrict_to_normal"] is False


@pytest.mark.asyncio
async def test_unbind_observer_leaves_observations_readable(monkeypatch) -> None:
    """T-5 (docs/tasks/2026-07-22-observation-binding-cleanup): removing the
    last observer binding never touches ``agent_observations`` —
    ChatroomService.remove_agent mutates only ``chatroom_agents`` and audits,
    and the rows stay fully listable through ObservationService afterward.
    Pins the root-cause boundary: this part of the stack is already correct
    per §5 of the dossier; the defect is confined to the frontend read path."""
    from contexts.conversation.application import chatroom_service as cs

    chatroom_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    obs = _observation(chatroom_id, agent_id)

    obs_repo_calls: list[str] = []

    class _SpyObsRepo:
        def __init__(self, db) -> None:
            pass

        async def list(self, *, chatroom_id, before=None, limit=50):
            obs_repo_calls.append("list")
            return [obs]

    monkeypatch.setattr(obs_svc, "ObservationRepository", _SpyObsRepo)
    monkeypatch.setattr(obs_svc, "ChatroomAgentRepository", lambda db: _FakeBindings())
    monkeypatch.setattr(obs_svc, "ChatroomRepository", lambda db: _FakeRooms())
    monkeypatch.setattr(obs_svc, "MessageRepository", lambda db: _FakeMessages())
    observation_service = obs_svc.ObservationService(object())

    class _AgentRepo:
        def __init__(self, db) -> None:
            pass

        async def remove(self, *, chatroom_id, agent_id, only_role=None):
            return True

    monkeypatch.setattr(cs, "ChatroomAgentRepository", _AgentRepo)
    monkeypatch.setattr(cs, "ChatroomRepository", lambda db: object())
    monkeypatch.setattr(cs, "ChatroomGuestRepository", lambda db: object())
    monkeypatch.setattr(cs, "WorkspaceRepository", lambda db: object())

    audits: list[str] = []

    async def _emit(db, event):
        audits.append(event.action)

    monkeypatch.setattr(cs.audit, "emit", _emit)

    chatroom_service = cs.ChatroomService(object())
    await chatroom_service.remove_agent(
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
    )

    assert audits == ["chatroom.agent_removed"]
    assert obs_repo_calls == []  # unbind never touches agent_observations

    rows = await observation_service.list(chatroom_id=chatroom_id)
    assert rows == [obs]
    assert obs_repo_calls == ["list"]


def _wire_patch_handler(monkeypatch, *, access, cap_calls, patched, roles=frozenset()):
    import app.api.v1.chatrooms as chatrooms_mod

    async def _pid(db, chatroom_id):
        return uuid.uuid4()

    async def _cap(db, principal, project_id, capability):
        cap_calls.append(capability)

    async def _resolve(db, *, principal, chatroom_id):
        if access is None:
            raise AssertionError("resolve_room_access must not run without a disclosure field")
        return access

    # V-4: a plain-flags patch resolves the caller's roles for the response
    # DTO's `is_moderator` without paying for a full `resolve_room_access`.
    class _Resolver:
        async def roles_for(self, principal, scope):
            return roles

    async def _get_resolver(db):
        return _Resolver()

    monkeypatch.setattr(chatrooms_mod, "get_role_resolver", _get_resolver)

    class _Service:
        def __init__(self, db) -> None:
            pass

        async def patch(self, **kw):
            patched.append(kw)
            return chatroom_row()

        async def rooms_with_observers(self, ids):
            return set()

        async def rooms_with_draft_readers(self, ids):
            return set()

    monkeypatch.setattr(chatrooms_mod, "_project_id_for_chatroom", _pid)
    monkeypatch.setattr(chatrooms_mod, "_require_project_cap", _cap)
    monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(chatrooms_mod, "ChatroomService", _Service)
    return chatrooms_mod


@pytest.mark.asyncio
async def test_disclosure_only_patch_skips_capability_gate(monkeypatch) -> None:
    """O-6 (R28.09): a creator demoted below project owner can still toggle
    disclosure — the capability gate is skipped for a disclosure-only patch."""
    import app.api.v1.chatrooms as chatrooms_mod

    uid = uuid.uuid4()
    cap_calls: list = []
    patched: list = []
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_MEMBER}))
    mod = _wire_patch_handler(monkeypatch, access=access, cap_calls=cap_calls, patched=patched)

    await mod.patch_chatroom(
        chatrooms_mod.ChatroomPatchIn(disclose_observers=False),
        chatroom_id=uuid.uuid4(),
        if_match="1",
        ctx=_CTX,
        principal=_principal(uid),
        db=_committing_db(),
    )
    assert cap_calls == []
    assert len(patched) == 1


@pytest.mark.asyncio
async def test_mixed_patch_keeps_capability_and_creator_gates(monkeypatch) -> None:
    import app.api.v1.chatrooms as chatrooms_mod

    cap_calls: list = []
    patched: list = []
    access = _access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER}))
    mod = _wire_patch_handler(monkeypatch, access=access, cap_calls=cap_calls, patched=patched)

    with pytest.raises(NotRoomCreator):
        await mod.patch_chatroom(
            chatrooms_mod.ChatroomPatchIn(name="renamed", disclose_observers=False),
            chatroom_id=uuid.uuid4(),
            if_match="1",
            ctx=_CTX,
            principal=_principal(),
            db=_committing_db(),
        )
    assert len(cap_calls) == 1
    assert patched == []


@pytest.mark.asyncio
async def test_name_only_patch_keeps_moderator_semantics(monkeypatch) -> None:
    import app.api.v1.chatrooms as chatrooms_mod

    cap_calls: list = []
    patched: list = []
    mod = _wire_patch_handler(
        monkeypatch,
        access=None,
        cap_calls=cap_calls,
        patched=patched,
        roles=frozenset({Role.PROJECT_OWNER}),
    )

    out = await mod.patch_chatroom(
        chatrooms_mod.ChatroomPatchIn(name="renamed"),
        chatroom_id=uuid.uuid4(),
        if_match="1",
        ctx=_CTX,
        principal=_principal(),
        db=_committing_db(),
    )
    assert len(cap_calls) == 1
    assert len(patched) == 1
    # V-4: the patch response reports the caller's real moderator standing,
    # resolved without the `resolve_room_access` the harness forbids here.
    assert out.is_moderator is True


# --------------------------------------------------------------------------- #
# T-1 (F-1, R28.09) — `chatroom.updated` on the room channel
#
# The room DTO carries `observers_present`, and until this event existed nothing
# could tell a live viewer it had changed: no writer in `chatrooms.py` published
# anything, so a participant learned they were being observed only on reload.
#
# The frame is constrained as hard as it is announced. `_pubsub_fanin`
# (`shared_kernel/realtime/connection.py`) delivers every room-channel frame to
# every subscriber, guests included, with no per-recipient filtering — so the
# payload must be an ids-only "refetch me" and each viewer re-GETs through
# `_to_out`, which is what re-applies the guest neutralisation per viewer. These
# tests therefore assert the payload's exact key set, not merely that the room id
# is present: a test that only checked for the id would pass on a frame that also
# leaked `disclose_observers`.
# --------------------------------------------------------------------------- #


def _spy_room_publisher(monkeypatch, mod):
    """Replace `chatrooms.Publisher` with the module-level spy and reset it."""
    _PublisherSpy.emitted = []
    monkeypatch.setattr(mod, "Publisher", _PublisherSpy)
    return _PublisherSpy


def _room_frames(chatroom_id):
    return [e for e in _PublisherSpy.emitted if e[0] == f"ws:room:{chatroom_id}"]


def _user_frames(user_id):
    return [e for e in _PublisherSpy.emitted if e[0] == f"ws:user:{user_id}"]


def _assert_ids_only_updated_frame(chatroom_id) -> None:
    """One `chatroom.updated` naming this room and carrying nothing else."""
    frames = _room_frames(chatroom_id)
    assert len(frames) == 1, f"expected exactly one room frame, got {frames}"
    _channel, event, payload = frames[0]
    assert event == "chatroom.updated"
    # Exact key set: the constraint is the ABSENCE of room content, so an
    # equality assertion is the only form that can enforce it.
    assert payload == {"chatroom_id": str(chatroom_id)}


def _assert_creator_only(chatroom_id, creator_id) -> None:
    """The creator's own sessions are refreshed and the room hears nothing.

    This is the shape that closes the observer-existence oracle: the frame that
    would have told every participant and guest "something you cannot see just
    happened" is not sent at all, while the creator's other tabs still update.
    """
    assert _room_frames(chatroom_id) == []
    frames = _user_frames(creator_id)
    assert len(frames) == 1, f"expected one creator frame, got {frames}"
    _channel, event, payload = frames[0]
    assert event == "chatroom.updated"
    assert payload == {"chatroom_id": str(chatroom_id)}


def _wire_add_handler(monkeypatch, *, access, added, project_id):
    import app.api.v1.chatrooms as chatrooms_mod

    async def _pid(db, chatroom_id):
        return project_id

    async def _cap(db, principal, project_id_, capability):
        return None

    async def _resolve(db, *, principal, chatroom_id):
        return access

    class _AgentsFacade:
        def __init__(self, db) -> None:
            pass

        async def get_agent(self, aid):
            return SimpleNamespace(id=aid, project_id=project_id)

    class _Service:
        def __init__(self, db) -> None:
            pass

        async def add_agent(self, **kw):
            added.append(kw)

    monkeypatch.setattr(chatrooms_mod, "_project_id_for_chatroom", _pid)
    monkeypatch.setattr(chatrooms_mod, "_require_project_cap", _cap)
    monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(chatrooms_mod, "AgentsFacade", _AgentsFacade)
    monkeypatch.setattr(chatrooms_mod, "ChatroomService", _Service)
    return chatrooms_mod


def _wire_role_patch_handler(monkeypatch, *, access, changed, role_calls):
    import app.api.v1.chatrooms as chatrooms_mod

    async def _resolve(db, *, principal, chatroom_id):
        return access

    class _Service:
        def __init__(self, db) -> None:
            pass

        async def set_agent_role(self, **kw):
            role_calls.append(kw)
            return changed

    monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(chatrooms_mod, "ChatroomService", _Service)
    return chatrooms_mod


def _committing_db():
    """A db double that records commit order against the publish."""
    return SimpleNamespace(commit=AsyncMock())


@pytest.mark.asyncio
async def test_bind_observer_in_a_disclosing_room_emits_to_the_room(monkeypatch) -> None:
    uid = uuid.uuid4()
    chatroom_id = uuid.uuid4()
    added: list = []
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    mod = _wire_add_handler(monkeypatch, access=access, added=added, project_id=uuid.uuid4())
    _spy_room_publisher(monkeypatch, mod)
    db = _committing_db()

    await mod.add_chatroom_agent(
        body=mod.AgentRef(agent_id=uuid.uuid4(), role="observer"),
        chatroom_id=chatroom_id,
        ctx=_CTX,
        principal=_principal(uid),
        db=db,
    )

    assert len(added) == 1
    # Disclosure is on, so `observers_present` really does change for every
    # participant — telling them is the whole point of [R28.09].
    _assert_ids_only_updated_frame(chatroom_id)
    # The dependency's commit runs only after the handler returns, so a frame
    # published before an explicit commit would tell viewers to re-read a write
    # a later rollback could still undo.
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_bind_observer_with_disclosure_off_is_silent_on_the_room(monkeypatch) -> None:
    """FU-8's fix, and the sharpest case in it.

    `_to_out` forces `observers_present` false for every viewer while disclosure
    is off, and filters the observer row out of the agent listing for
    non-creators. So this write moves nothing they can see — and a room-channel
    frame, which reaches every subscriber including pure guests, would tell them
    that an invisible write had just happened. In a room with disclosure off the
    only invisible write is an observer binding, which is exactly what [R28.10]
    and O-8's guest neutralisation exist to withhold.
    """
    uid = uuid.uuid4()
    chatroom_id = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}), disclose_observers=False)
    mod = _wire_add_handler(monkeypatch, access=access, added=[], project_id=uuid.uuid4())
    _spy_room_publisher(monkeypatch, mod)

    await mod.add_chatroom_agent(
        body=mod.AgentRef(agent_id=uuid.uuid4(), role="observer"),
        chatroom_id=chatroom_id,
        ctx=_CTX,
        principal=_principal(uid),
        db=_committing_db(),
    )

    _assert_creator_only(chatroom_id, uid)


@pytest.mark.asyncio
async def test_bind_normal_agent_is_room_visible_whatever_the_disclosure(monkeypatch) -> None:
    """A normal binding adds a row every viewer's listing shows, so withholding
    the frame would leave that listing stale for no privacy gain."""
    uid = uuid.uuid4()
    chatroom_id = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}), disclose_observers=False)
    mod = _wire_add_handler(monkeypatch, access=access, added=[], project_id=uuid.uuid4())
    _spy_room_publisher(monkeypatch, mod)

    await mod.add_chatroom_agent(
        body=mod.AgentRef(agent_id=uuid.uuid4(), role="normal"),
        chatroom_id=chatroom_id,
        ctx=_CTX,
        principal=_principal(uid),
        db=_committing_db(),
    )

    _assert_ids_only_updated_frame(chatroom_id)


@pytest.mark.asyncio
async def test_agent_role_patch_emits_ids_only_chatroom_updated(monkeypatch) -> None:
    uid = uuid.uuid4()
    chatroom_id = uuid.uuid4()
    role_calls: list = []
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    mod = _wire_role_patch_handler(monkeypatch, access=access, changed=True, role_calls=role_calls)
    _spy_room_publisher(monkeypatch, mod)
    db = _committing_db()

    await mod.patch_chatroom_agent_role(
        body=mod.AgentRolePatchIn(role="observer"),
        chatroom_id=chatroom_id,
        agent_id=uuid.uuid4(),
        ctx=_CTX,
        principal=_principal(uid),
        db=db,
    )

    assert len(role_calls) == 1
    _assert_ids_only_updated_frame(chatroom_id)


@pytest.mark.asyncio
async def test_agent_role_patch_on_unbound_agent_emits_nothing(monkeypatch) -> None:
    """A 404 is not a room change. `set_agent_role` reporting no row means
    nothing was written, so announcing a refetch would be a lie every viewer
    pays a GET for."""
    uid = uuid.uuid4()
    chatroom_id = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    mod = _wire_role_patch_handler(monkeypatch, access=access, changed=False, role_calls=[])
    _spy_room_publisher(monkeypatch, mod)

    with pytest.raises(HTTPException) as exc:
        await mod.patch_chatroom_agent_role(
            body=mod.AgentRolePatchIn(role="observer"),
            chatroom_id=chatroom_id,
            agent_id=uuid.uuid4(),
            ctx=_CTX,
            principal=_principal(uid),
            db=_committing_db(),
        )
    assert exc.value.status_code == 404
    assert _room_frames(chatroom_id) == []


def _stub_role(monkeypatch, mod, role):
    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def agent_role_in_chatroom(self, *, chatroom_id, agent_id):
            return role

    monkeypatch.setattr(mod, "ConversationFacade", _Facade)


@pytest.mark.asyncio
async def test_unbind_normal_agent_emits_ids_only_chatroom_updated(monkeypatch) -> None:
    uid = uuid.uuid4()
    chatroom_id = uuid.uuid4()
    removed: list = []
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    mod = _wire_remove_handler(monkeypatch, access=access, removed=removed)
    _stub_role(monkeypatch, mod, ChatroomAgentRole.NORMAL)
    _spy_room_publisher(monkeypatch, mod)

    await mod.remove_chatroom_agent(
        chatroom_id=chatroom_id,
        agent_id=uuid.uuid4(),
        ctx=_CTX,
        principal=_principal(uid),
        db=_committing_db(),
    )

    assert len(removed) == 1
    _assert_ids_only_updated_frame(chatroom_id)


@pytest.mark.asyncio
async def test_creator_unbinding_an_observer_with_disclosure_off_is_silent(monkeypatch) -> None:
    """The removal half of FU-8's fix: invisible to non-creators, so unannounced
    to them."""
    uid = uuid.uuid4()
    chatroom_id = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}), disclose_observers=False)
    mod = _wire_remove_handler(monkeypatch, access=access, removed=[])
    _stub_role(monkeypatch, mod, ChatroomAgentRole.OBSERVER)
    _spy_room_publisher(monkeypatch, mod)

    await mod.remove_chatroom_agent(
        chatroom_id=chatroom_id,
        agent_id=uuid.uuid4(),
        ctx=_CTX,
        principal=_principal(uid),
        db=_committing_db(),
    )

    _assert_creator_only(chatroom_id, uid)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [ChatroomAgentRole.NORMAL, ChatroomAgentRole.OBSERVER])
async def test_a_non_creator_unbind_emits_the_same_frame_for_either_role(monkeypatch, role) -> None:
    """O-5 over the new event. A non-creator's unbind is role-scoped, so an
    observer target is a silent no-op — and the frame must not become the oracle
    the silent 204 exists to prevent. It is therefore emitted unconditionally
    here, making its presence a constant that answers nothing about the target.
    """
    chatroom_id = uuid.uuid4()
    access = _access(
        created_by=uuid.uuid4(),
        roles=frozenset({Role.PROJECT_OWNER}),
        disclose_observers=False,
    )
    mod = _wire_remove_handler(monkeypatch, access=access, removed=[])
    _stub_role(monkeypatch, mod, role)
    _spy_room_publisher(monkeypatch, mod)

    await mod.remove_chatroom_agent(
        chatroom_id=chatroom_id,
        agent_id=uuid.uuid4(),
        ctx=_CTX,
        principal=_principal(),
        db=_committing_db(),
    )

    _assert_ids_only_updated_frame(chatroom_id)


@pytest.mark.asyncio
async def test_disclosure_patch_emits_ids_only_chatroom_updated(monkeypatch) -> None:
    uid = uuid.uuid4()
    chatroom_id = uuid.uuid4()
    access = _access(created_by=uid, roles=frozenset({Role.PROJECT_OWNER}))
    mod = _wire_patch_handler(monkeypatch, access=access, cap_calls=[], patched=[])
    _spy_room_publisher(monkeypatch, mod)

    await mod.patch_chatroom(
        mod.ChatroomPatchIn(disclose_observers=False),
        chatroom_id=chatroom_id,
        if_match="1",
        ctx=_CTX,
        principal=_principal(uid),
        db=_committing_db(),
    )

    _assert_ids_only_updated_frame(chatroom_id)


@pytest.mark.asyncio
async def test_draft_access_grant_emits_ids_only_chatroom_updated(monkeypatch) -> None:
    """`drafts_readable` is the other half of the pair `disclose_drafts` belongs to
    ([R32.05]), and this route is what flips `has_draft_readers` under it. Without
    this emit the pair is half-fresh: toggling the disclosure refreshes every live
    viewer's "an agent here can read what you are typing" chip, while granting the
    reading itself leaves the chip absent until reload — on the field participants
    have the strongest interest in."""
    _, chatroom_id, _uid = await _run_draft_access(monkeypatch, disclose_drafts=True)
    _assert_ids_only_updated_frame(chatroom_id)


async def _run_draft_access(monkeypatch, *, disclose_drafts):
    import app.api.v1.chatrooms as chatrooms_mod

    uid = uuid.uuid4()
    chatroom_id = uuid.uuid4()
    access = _access(
        created_by=uid,
        roles=frozenset({Role.PROJECT_OWNER}),
        disclose_drafts=disclose_drafts,
    )

    async def _resolve(db, *, principal, chatroom_id):
        return access

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def set_agent_draft_grant(self, **kw):
            return True

    monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(chatrooms_mod, "ConversationFacade", _Facade)
    _spy_room_publisher(monkeypatch, chatrooms_mod)

    await chatrooms_mod.patch_chatroom_agent_draft_access(
        body=chatrooms_mod.AgentDraftAccessIn(granted=True),
        chatroom_id=chatroom_id,
        agent_id=uuid.uuid4(),
        ctx=_CTX,
        principal=_principal(uid),
        db=_committing_db(),
    )
    return chatrooms_mod, chatroom_id, uid


@pytest.mark.asyncio
async def test_draft_access_grant_with_disclosure_off_is_silent(monkeypatch) -> None:
    """`drafts_readable` is `disclose_drafts AND has_draft_readers`, so with the
    disclosure off this grant moves nothing a participant can see — and the frame
    would announce it anyway ([R32.05])."""
    _, chatroom_id, uid = await _run_draft_access(monkeypatch, disclose_drafts=False)
    _assert_creator_only(chatroom_id, uid)


@pytest.mark.asyncio
async def test_a_rename_emits_ids_only_chatroom_updated(monkeypatch) -> None:
    """A rename moves `name`, which every viewer reads, so it is announced.

    This inverts the assertion that stood here while the emit was scoped to the
    disclosure fields. The reason the narrow gate was safe to widen is that *every*
    field `ChatroomPatchIn` accepts is in the DTO a non-creator reads and in the
    one a pure guest reads — `_to_out` conditions on the viewer for only
    `created_by_user_id`, `disclose_observers`, `observers_present` and
    `is_moderator`, none of them patchable. So no patch can produce the
    frame-with-an-unchanged-DTO that the room-visible gate exists to withhold.
    """
    chatroom_id = uuid.uuid4()
    mod = _wire_patch_handler(
        monkeypatch,
        access=None,
        cap_calls=[],
        patched=[],
        roles=frozenset({Role.PROJECT_OWNER}),
    )
    _spy_room_publisher(monkeypatch, mod)

    await mod.patch_chatroom(
        mod.ChatroomPatchIn(name="renamed"),
        chatroom_id=chatroom_id,
        if_match="1",
        ctx=_CTX,
        principal=_principal(),
        db=_committing_db(),
    )

    _assert_ids_only_updated_frame(chatroom_id)


@pytest.mark.asyncio
async def test_an_empty_patch_still_emits_nothing(monkeypatch) -> None:
    """The widened gate is `if fields:`, not `if True:`.

    A patch naming no field changes nothing, so it must stay silent — the same
    truthiness the capability gate above already relies on.
    """
    chatroom_id = uuid.uuid4()
    mod = _wire_patch_handler(
        monkeypatch,
        access=None,
        cap_calls=[],
        patched=[],
        roles=frozenset({Role.PROJECT_OWNER}),
    )
    _spy_room_publisher(monkeypatch, mod)

    await mod.patch_chatroom(
        mod.ChatroomPatchIn(),
        chatroom_id=chatroom_id,
        if_match="1",
        ctx=_CTX,
        principal=_principal(),
        db=_committing_db(),
    )

    assert _PublisherSpy.emitted == []


# --------------------------------------------------------------------------- #
# ObservationService.release (R28.06/R28.07/R28.08/R28.11)
# --------------------------------------------------------------------------- #


def _observation(chatroom_id, agent_id, *, released=False):
    return AgentObservation(
        id=uuid.uuid4(),
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        content_md="the analysis",
        trigger="every_n_messages",
        released_at=None if not released else object(),  # only truthiness is read
    )


class _FakeObsRepo:
    def __init__(self, observation, *, cas_wins=True):
        self.observation = observation
        self.cas_wins = cas_wins
        self.released_with: dict | None = None
        self.message_target: dict | None = None

    async def get(self, *, chatroom_id, observation_id):
        o = self.observation
        if o is None or o.id != observation_id or o.chatroom_id != chatroom_id:
            return None
        return o

    async def mark_released(self, *, chatroom_id, observation_id, released_by_user_id, release_target):
        if not self.cas_wins:
            return False
        self.released_with = {
            "released_by_user_id": released_by_user_id,
            "release_target": release_target,
        }
        return True

    async def mark_release_message(self, *, chatroom_id, observation_id, release_target):
        self.message_target = release_target


class _FakeBindings:
    def __init__(self, normal=(), observer=()):
        self._rows = [SimpleNamespace(agent_id=a, role=ChatroomAgentRole.NORMAL) for a in normal] + [
            SimpleNamespace(agent_id=a, role=ChatroomAgentRole.OBSERVER) for a in observer
        ]

    async def list(self, chatroom_id):
        return self._rows


class _FakeRooms:
    def __init__(self, *, disclose=True, created_by=None):
        self._room = SimpleNamespace(disclose_observers=disclose, created_by_user_id=created_by)

    async def get(self, chatroom_id):
        return self._room


class _FakeMessages:
    def __init__(self):
        self.created: list[dict] = []

    async def create(self, *, chatroom_id, sender_type, sender_id, content_md, metadata=None):
        self.created.append(
            {
                "sender_type": sender_type,
                "sender_id": sender_id,
                "content_md": content_md,
                "metadata": metadata or {},
            }
        )
        return SimpleNamespace(
            id=uuid.uuid4(),
            chatroom_id=chatroom_id,
            sender_type=sender_type,
            sender_id=sender_id,
            content_md=content_md,
            metadata=metadata or {},
            created_at=None,
        )


def _wire_service(
    monkeypatch,
    *,
    observation,
    normal_agents=(),
    observer_agents=(),
    disclose=True,
    cas_wins=True,
):
    room_id = observation.chatroom_id if observation else uuid.uuid4()
    obs_repo = _FakeObsRepo(observation, cas_wins=cas_wins)
    bindings = _FakeBindings(normal=normal_agents, observer=observer_agents)
    rooms = _FakeRooms(disclose=disclose)
    messages = _FakeMessages()
    audits: list = []
    pushes: list = []

    monkeypatch.setattr(obs_svc, "ObservationRepository", lambda db: obs_repo)
    monkeypatch.setattr(obs_svc, "ChatroomAgentRepository", lambda db: bindings)
    monkeypatch.setattr(obs_svc, "ChatroomRepository", lambda db: rooms)
    monkeypatch.setattr(obs_svc, "MessageRepository", lambda db: messages)

    async def _emit(db, event):
        audits.append(event)

    monkeypatch.setattr(obs_svc.audit, "emit", _emit)

    async def _push(agent_id, note):
        pushes.append((agent_id, note))

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.push", _push)

    service = obs_svc.ObservationService(object())
    return service, room_id, obs_repo, messages, audits, pushes


@pytest.mark.asyncio
async def test_release_to_room_creates_system_message(monkeypatch) -> None:
    observer_id = uuid.uuid4()
    obs = _observation(uuid.uuid4(), observer_id)
    service, room_id, obs_repo, messages, _audits, pushes = _wire_service(
        monkeypatch, observation=obs, disclose=True
    )

    result = await service.release(
        chatroom_id=room_id,
        observation_id=obs.id,
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
        target_room=True,
    )

    assert len(messages.created) == 1
    created = messages.created[0]
    assert created["sender_type"] is SenderType.SYSTEM
    assert created["sender_id"] is None
    assert created["content_md"] == "the analysis"
    assert created["metadata"]["type"] == "released_observation"
    # Disclosure on → observer identity travels (R28.09).
    assert created["metadata"]["observer_agent_id"] == str(observer_id)
    assert obs_repo.message_target is not None
    assert obs_repo.message_target["kind"] == "room"
    assert "message_id" in obs_repo.message_target
    assert result.message is not None
    assert pushes == []


@pytest.mark.asyncio
async def test_releasing_a_block_carrying_observation_is_the_same_message(monkeypatch) -> None:
    """AC-10. Blocks do not reach the room ([R28.15] Q-6): release still reads
    `content_md`, which for a block-carrying observation is the serialisation the
    creator was shown. The message shape, the metadata and the disclosure rule are
    the ones that already shipped, and the release dialog's plain-text override
    still edits plain text."""
    observer_id = uuid.uuid4()
    obs = replace(
        _observation(uuid.uuid4(), observer_id),
        content_md="### Three things\n\n- one\n\n_Basis: read off what was said._",
        blocks=[{"kind": "key_points", "basis": "transcript", "points": [{"text": "one"}]}],
    )
    service, room_id, _repo, messages, _audits, _pushes = _wire_service(
        monkeypatch, observation=obs, disclose=True
    )

    await service.release(
        chatroom_id=room_id,
        observation_id=obs.id,
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
        target_room=True,
    )

    created = messages.created[0]
    assert created["sender_type"] is SenderType.SYSTEM
    assert created["content_md"] == obs.content_md
    assert created["metadata"]["type"] == "released_observation"
    # Nothing block-shaped crosses into `messages`.
    assert "blocks" not in created["metadata"]
    assert "kind" not in created["metadata"]


@pytest.mark.asyncio
async def test_a_release_override_replaces_the_serialisation(monkeypatch) -> None:
    """AC-10's second half: the override is still plain text over `content_md`."""
    obs = replace(
        _observation(uuid.uuid4(), uuid.uuid4()),
        content_md="### Three things\n\n- one",
        blocks=[{"kind": "key_points", "basis": "transcript", "points": [{"text": "one"}]}],
    )
    service, room_id, _repo, messages, _audits, _pushes = _wire_service(
        monkeypatch, observation=obs, disclose=False
    )

    await service.release(
        chatroom_id=room_id,
        observation_id=obs.id,
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
        target_room=True,
        content_override="what I actually want the class to read",
    )

    assert messages.created[0]["content_md"] == "what I actually want the class to read"


@pytest.mark.asyncio
async def test_release_to_room_undisclosed_hides_observer_identity(monkeypatch) -> None:
    obs = _observation(uuid.uuid4(), uuid.uuid4())
    service, room_id, _repo, messages, _audits, _pushes = _wire_service(
        monkeypatch, observation=obs, disclose=False
    )

    await service.release(
        chatroom_id=room_id,
        observation_id=obs.id,
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
        target_room=True,
    )

    assert "observer_agent_id" not in messages.created[0]["metadata"]


@pytest.mark.asyncio
async def test_release_to_agents_defers_push_and_resolves_content(monkeypatch) -> None:
    """O-1 (F-1): release() must NOT touch Redis pre-commit — the push happens
    post-commit in _dispatch_release. The service resolves the override into
    ReleaseResult.content so the dispatcher can build the note."""
    target = uuid.uuid4()
    obs = _observation(uuid.uuid4(), uuid.uuid4())
    service, room_id, _repo, messages, _audits, pushes = _wire_service(
        monkeypatch, observation=obs, normal_agents=(target,)
    )

    result = await service.release(
        chatroom_id=room_id,
        observation_id=obs.id,
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
        target_room=False,
        target_agent_ids=[target],
        wake=True,
        content_override="edited analysis",
    )

    # R28.07: never a room message; R28.08: no delivery before the commit.
    assert messages.created == []
    assert pushes == []
    assert result.content == "edited analysis"
    assert result.wake is True
    assert result.target_agent_ids == (target,)


def test_release_in_rejects_whitespace_only_override() -> None:
    """O-9 (P-9): a content_override that is empty after strip is a 422, and a
    padded override is stored stripped."""
    from pydantic import ValidationError

    from app.api.v1.observations import ReleaseIn

    with pytest.raises(ValidationError):
        ReleaseIn(target="room", content_override="   ")
    assert ReleaseIn(target="room", content_override=" x ").content_override == "x"


@pytest.mark.asyncio
async def test_dispatch_release_pushes_per_target_best_effort(monkeypatch) -> None:
    """O-1 (F-1): the post-commit dispatcher pushes once per target with the
    resolved content; one target's Redis failure neither raises nor blocks the
    remaining targets (mirrors the room path's best-effort discipline)."""
    from app.api.v1 import observations as obs_api

    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    pushes: list = []

    async def _push(agent_id, note):
        if agent_id == b:
            raise RuntimeError("redis down")
        pushes.append((agent_id, note))

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.push", _push)

    result = obs_svc.ReleaseResult(
        observation=SimpleNamespace(id=uuid.uuid4(), release_target={"kind": "agents"}),
        message=None,
        target_agent_ids=(a, b, c),
        wake=False,
        content="the analysis",
    )

    class _Svc:
        async def recipient_user_id(self, chatroom_id):
            return None

    room_id = uuid.uuid4()
    await obs_api._dispatch_release(object(), chatroom_id=room_id, service=_Svc(), result=result)

    assert [agent for agent, _ in pushes] == [a, c]
    for _, note in pushes:
        assert note["kind"] == "released_observation"
        assert note["chatroom_id"] == str(room_id)
        assert note["content"] == "the analysis"


def _wire_delete_handler(monkeypatch, *, recipient, deleted, raises=None):
    """Stub `delete_observation`'s creator gate and service for handler tests."""
    import app.api.v1.observations as obs_api

    async def _require(db, *, principal, chatroom_id):
        return None

    class _Service:
        def __init__(self, db) -> None:
            pass

        async def delete(self, **kw):
            if raises is not None:
                raise raises
            deleted.append(kw)

        async def recipient_user_id(self, chatroom_id):
            return recipient

    monkeypatch.setattr(obs_api, "_require_creator", _require)
    monkeypatch.setattr(obs_api, "ObservationService", _Service)
    _PublisherSpy.emitted = []
    monkeypatch.setattr(obs_api, "Publisher", _PublisherSpy)
    return obs_api


@pytest.mark.asyncio
async def test_delete_observation_emits_to_the_creator_user_channel(monkeypatch) -> None:
    """T-13 (F-14). Release publishes `observation.released` and delete published
    nothing, so a second session kept rendering a row the server no longer had.
    The frame goes on the creator's own user channel exactly like the release
    one — never the room channel, whose subscribers include guests (Q-9)."""
    creator = uuid.uuid4()
    chatroom_id = uuid.uuid4()
    observation_id = uuid.uuid4()
    deleted: list = []
    mod = _wire_delete_handler(monkeypatch, recipient=creator, deleted=deleted)
    db = _committing_db()

    await mod.delete_observation(
        chatroom_id=chatroom_id,
        observation_id=observation_id,
        ctx=_CTX,
        principal=_principal(creator),
        db=db,
    )

    assert len(deleted) == 1
    # The dependency's commit runs only after the handler returns, so a frame
    # published before an explicit commit would tell the other session to
    # re-read a delete a later rollback could still undo.
    db.commit.assert_awaited()
    assert _room_frames(chatroom_id) == []
    frames = _user_frames(creator)
    assert len(frames) == 1, f"expected one creator frame, got {frames}"
    _channel, event, payload = frames[0]
    assert event == "observation.deleted"
    # Equality, not membership: the payload is ids-only by contract.
    assert payload == {
        "chatroom_id": str(chatroom_id),
        "observation_id": str(observation_id),
    }


@pytest.mark.asyncio
async def test_delete_observation_publish_failure_does_not_fail_the_delete(monkeypatch) -> None:
    """Best-effort, mirroring `_dispatch_release`: the row is already committed,
    so a Redis hiccup must not surface as a failed delete."""
    creator = uuid.uuid4()
    deleted: list = []
    mod = _wire_delete_handler(monkeypatch, recipient=creator, deleted=deleted)

    class _BoomPublisher:
        def __init__(self, channel: str) -> None:
            pass

        async def emit(self, event, payload):
            raise RuntimeError("redis down")

    monkeypatch.setattr(mod, "Publisher", _BoomPublisher)

    await mod.delete_observation(
        chatroom_id=uuid.uuid4(),
        observation_id=uuid.uuid4(),
        ctx=_CTX,
        principal=_principal(creator),
        db=_committing_db(),
    )

    assert len(deleted) == 1


@pytest.mark.asyncio
async def test_delete_observation_with_no_resolvable_creator_publishes_nothing(monkeypatch) -> None:
    """A NULL-creator room has no user channel to address — the same guard the
    release path applies at `_dispatch_release`."""
    mod = _wire_delete_handler(monkeypatch, recipient=None, deleted=[])

    await mod.delete_observation(
        chatroom_id=uuid.uuid4(),
        observation_id=uuid.uuid4(),
        ctx=_CTX,
        principal=_principal(uuid.uuid4()),
        db=_committing_db(),
    )

    assert _PublisherSpy.emitted == []


@pytest.mark.asyncio
async def test_delete_observation_not_found_neither_commits_nor_emits(monkeypatch) -> None:
    """A 404 must not announce a delete that did not happen."""
    from contexts.conversation.domain.errors import ObservationNotFound

    mod = _wire_delete_handler(
        monkeypatch,
        recipient=uuid.uuid4(),
        deleted=[],
        raises=ObservationNotFound(),
    )
    db = _committing_db()

    with pytest.raises(ObservationNotFound):
        await mod.delete_observation(
            chatroom_id=uuid.uuid4(),
            observation_id=uuid.uuid4(),
            ctx=_CTX,
            principal=_principal(uuid.uuid4()),
            db=db,
        )

    db.commit.assert_not_awaited()
    assert _PublisherSpy.emitted == []


@pytest.mark.asyncio
async def test_release_rejects_observer_and_unbound_targets(monkeypatch) -> None:
    normal, observer, unbound = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    obs = _observation(uuid.uuid4(), observer)
    service, room_id, *_rest = _wire_service(
        monkeypatch, observation=obs, normal_agents=(normal,), observer_agents=(observer,)
    )

    for bad in (observer, unbound):
        with pytest.raises(InvalidReleaseTarget):
            await service.release(
                chatroom_id=room_id,
                observation_id=obs.id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
                target_room=False,
                target_agent_ids=[normal, bad],
            )


@pytest.mark.asyncio
async def test_release_requires_targets_for_agents(monkeypatch) -> None:
    obs = _observation(uuid.uuid4(), uuid.uuid4())
    service, room_id, *_rest = _wire_service(monkeypatch, observation=obs)
    with pytest.raises(InvalidReleaseTarget):
        await service.release(
            chatroom_id=room_id,
            observation_id=obs.id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            target_room=False,
            target_agent_ids=[],
        )


@pytest.mark.asyncio
async def test_double_release_loses_cas(monkeypatch) -> None:
    obs = _observation(uuid.uuid4(), uuid.uuid4())
    service, room_id, _repo, messages, _audits, _pushes = _wire_service(
        monkeypatch, observation=obs, cas_wins=False
    )
    with pytest.raises(ObservationAlreadyReleased):
        await service.release(
            chatroom_id=room_id,
            observation_id=obs.id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            target_room=True,
        )
    # The CAS loser must not have inserted a message (R28.08).
    assert messages.created == []


@pytest.mark.asyncio
async def test_release_unknown_observation_404(monkeypatch) -> None:
    obs = _observation(uuid.uuid4(), uuid.uuid4())
    service, room_id, *_rest = _wire_service(monkeypatch, observation=obs)
    with pytest.raises(ObservationNotFound):
        await service.release(
            chatroom_id=room_id,
            observation_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            target_room=True,
        )


@pytest.mark.asyncio
async def test_release_audit_never_contains_content(monkeypatch) -> None:
    obs = _observation(uuid.uuid4(), uuid.uuid4())
    service, room_id, _repo, _messages, audits, _pushes = _wire_service(monkeypatch, observation=obs)
    await service.release(
        chatroom_id=room_id,
        observation_id=obs.id,
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
        target_room=True,
        content_override="SECRET-OVERRIDE",
    )
    released = [e for e in audits if e.action == "observation.released"]
    assert len(released) == 1
    flat = repr(released[0].metadata)
    assert "the analysis" not in flat
    assert "SECRET-OVERRIDE" not in flat
    assert released[0].metadata["content_overridden"] is True


# --------------------------------------------------------------------------- #
# Turn engine — observer leak pin (R28.01/R28.03/R28.05/R28.13)
# --------------------------------------------------------------------------- #


class _FakeSavepoint:
    """Stands in for the SQLAlchemy AsyncSessionTransaction returned by
    ``begin_nested()``: an async context manager, not itself awaited."""

    async def __aenter__(self) -> _FakeSavepoint:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False  # never swallow — matches real SAVEPOINT semantics


class _FakeDB:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    def begin_nested(self) -> _FakeSavepoint:
        return _FakeSavepoint()


class _PublisherSpy:
    """Records every (channel, event, payload) across all constructions."""

    emitted: ClassVar[list[tuple[str, str, dict]]] = []

    def __init__(self, channel: str) -> None:
        self._channel = channel

    async def emit(self, event: str, payload: dict) -> None:
        _PublisherSpy.emitted.append((self._channel, event, payload))


def _observer_agent():
    return SimpleNamespace(
        id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        system_prompt="prompt",
        model_hint=SimpleNamespace(value="claude"),
        model_id=None,
        context_mode=SimpleNamespace(value="general"),
        context_token_cap=None,
    )


def _wire_observer_engine(monkeypatch, agent, *, creator_id, bound_skills=()):
    """Wire a full `_run_locked` pass for an observer-role binding, with fakes
    at every external boundary and a global Publisher spy."""
    _PublisherSpy.emitted = []
    monkeypatch.setattr(te, "Publisher", _PublisherSpy)

    class _AgentsFacade:
        def __init__(self, db) -> None:
            pass

        async def get_agent(self, aid):
            return agent

        async def list_agent_tools(self, aid):
            # The turn resolves the agent's tools once and shares the snapshot with the
            # built-in tool assembly, the staging gate, and the skills tap. These tests
            # configure no tools.
            return []

    monkeypatch.setattr(te, "AgentsFacade", _AgentsFacade)

    class _BindingRepo:
        def __init__(self, db) -> None:
            pass

        async def role_of(self, *, chatroom_id, agent_id):
            return ChatroomAgentRole.OBSERVER

    monkeypatch.setattr(te, "ChatroomAgentRepository", _BindingRepo)

    class _KeysFacade:
        def __init__(self, db) -> None:
            pass

        async def get_key_group(self, kgid):
            return SimpleNamespace(project_id=agent.project_id)

        async def has_carried_provider_in_group(self, kgid, provider):
            return True

    monkeypatch.setattr(te, "KeysFacade", _KeysFacade)

    class _BoomMessageService:
        def __init__(self, db) -> None:
            raise AssertionError("observer turns must never construct MessageService")

    monkeypatch.setattr(te, "MessageService", _BoomMessageService)

    recorded: dict = {}

    class _ObsService:
        def __init__(self, db) -> None:
            pass

        async def record(self, **kw):
            recorded.update(kw)
            return SimpleNamespace(id=uuid.uuid4(), created_at=None)

        async def recipient_user_id(self, chatroom_id):
            return creator_id

    monkeypatch.setattr(te, "ObservationService", _ObsService)

    # The §31 turn-time tap runs on every room turn, an observer's included. Nothing
    # is bound by default, and an empty index renders to "" — no block, no tokens.
    class _SkillsFacade:
        def __init__(self, db) -> None:
            pass

        async def resolve_bound_set(self, *, agent_id, agent_project_id, enabled_tools):
            return BoundSet(skills=tuple(bound_skills))

        @staticmethod
        def render_index(skills):
            return "\n".join(f"- {s.name}: {s.description}" for s in skills)

    monkeypatch.setattr(te, "SkillsFacade", _SkillsFacade)
    monkeypatch.setattr(te, "build_registry", lambda *a, **k: SimpleNamespace(specs=lambda: []))

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]
    engine._compact_forced_rooms = {}  # type: ignore[attr-defined]

    async def _noop(*a, **k):
        return None

    async def _true(*a, **k):
        return True

    async def _history(
        agent_, chatroom_id, context_limit, provider, model, *, extra_projected_tokens=0, room=None
    ):
        return [
            SimpleNamespace(
                role="user", content="hello", sender_id=uuid.uuid4(), id=uuid.uuid4(), token_count=2
            )
        ]

    async def _labels(agent_, chatroom_id, history, **k):
        return {}, {}

    async def _labels_empty(*a, **k):
        return {}

    async def _none(*a, **k):
        return None

    async def _no_staging(*a, **k):
        # `_stage_workspace_inputs` returns (note, unstaged): no note, and no skill whose
        # scripts failed to reach the volume.
        return None, []

    async def _empty_list(*a, **k):
        return []

    async def _pending(agent_, chatroom_id_):
        return None, [], [], set()

    stream_seen: dict = {}

    async def _stream(**kw):
        stream_seen.update(kw)
        return te.ToolLoopOutcome(text="private analysis", rounds=1)

    async def _memory(agent_, chatroom_id):
        return "[Your previous observations]\n- earlier"

    engine._audit = _noop  # type: ignore[attr-defined]
    engine._turn_rate_allowed = _true  # type: ignore[attr-defined]
    engine._assemble_history = _history  # type: ignore[attr-defined]
    engine._participant_labels = _labels  # type: ignore[attr-defined]
    engine._room_guest_names = _labels_empty  # type: ignore[attr-defined]
    engine._room_owner_label = _none  # type: ignore[attr-defined]
    engine._rag_context = _none  # type: ignore[attr-defined]
    engine._graphrag_context = _none  # type: ignore[attr-defined]
    engine._knowmap_context = _none  # type: ignore[attr-defined]
    engine._activity_context = _none  # type: ignore[attr-defined]
    engine._pending_context_and_tools = _pending  # type: ignore[attr-defined]
    engine._builtin_tools = _empty_list  # type: ignore[attr-defined]
    engine._resolve_trigger_attachments = _none  # type: ignore[attr-defined]
    engine._stage_workspace_inputs = _no_staging  # type: ignore[attr-defined]
    engine._model_attachment_blocks = _empty_list  # type: ignore[attr-defined]
    engine._observer_memory_block = _memory  # type: ignore[attr-defined]
    engine._stream_with_tools = _stream  # type: ignore[attr-defined]
    engine._provider_message = (  # type: ignore[attr-defined]
        lambda hm, aid, an, un, attachment_blocks=None: {"role": "user", "content": hm.content}
    )

    async def _boom(*a, **k):
        raise AssertionError("observer turns must not dispatch message signals / reply wakeups")

    engine._dispatch_agent_message_signal = _boom  # type: ignore[attr-defined]
    engine._dispatch_agent_reply_wakeups = _boom  # type: ignore[attr-defined]
    engine._persist_artifacts = _boom  # type: ignore[attr-defined]

    return engine, recorded, stream_seen


@pytest.mark.asyncio
async def test_observer_turn_emits_nothing_on_room_channel(monkeypatch) -> None:
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, recorded, stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)
    room_id = uuid.uuid4()

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=room_id,
        trigger="every_n_messages",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "completed"
    assert result.message_id is None
    assert result.text == "private analysis"

    # The leak pin: zero events on any room channel, across the whole turn.
    room_events = [e for e in _PublisherSpy.emitted if e[0].startswith("ws:room:")]
    assert room_events == []
    # Creator channel got exactly started + created, ids only (R28.13).
    user_events = [e for e in _PublisherSpy.emitted if e[0] == f"ws:user:{creator}"]
    assert [e[1] for e in user_events] == ["observation.started", "observation.created"]
    for _ch, _ev, payload in user_events:
        assert "private analysis" not in repr(payload)

    # Token streaming was suppressed via the headless mechanism.
    assert stream_seen["room"] is None

    # Output persisted as an observation with the turn's trigger.
    assert recorded["content_md"] == "private analysis"
    assert recorded["trigger"] == "every_n_messages"
    assert recorded["chatroom_id"] == room_id


@pytest.mark.asyncio
async def test_observer_turn_folds_memory_and_framing_into_system(monkeypatch) -> None:
    agent = _observer_agent()
    engine, _recorded, stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=uuid.uuid4())

    await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="silence_minutes",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    system_text = stream_seen["system_text"]
    assert "[Observer role]" in system_text
    assert "[Your previous observations]" in system_text


@pytest.mark.asyncio
async def test_room_turn_folds_the_bound_skills_index_into_system(monkeypatch) -> None:
    # §31 AC-4 on the room path: the index reaches the request, and the tool that
    # reads a body is built from the same snapshot the tap validated.
    agent = _observer_agent()
    skill = make_skill(name="pdf-fill", description="Fills PDF forms.")
    engine, _recorded, stream_seen = _wire_observer_engine(
        monkeypatch, agent, creator_id=uuid.uuid4(), bound_skills=(skill,)
    )
    built: dict = {}
    monkeypatch.setattr(
        te, "build_registry", lambda *a, **k: built.update(k) or SimpleNamespace(specs=lambda: [])
    )

    await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="silence_minutes",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert "- pdf-fill: Fills PDF forms." in stream_seen["system_text"]
    # The whole snapshot reaches the registry, not just its skills: the bodies, the file
    # manifest, and the scan statuses that gate them must not be able to drift apart
    # between the tap and the tool.
    assert built["skills"].skills == (skill,)


@pytest.mark.asyncio
async def test_observer_turn_no_input_emits_observation_skipped(monkeypatch) -> None:
    """O-4 (P-2): benign skips emit observation.skipped, not observation.failed.
    The event must still fire so the creator's 'analyzing' indicator clears."""
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, _recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)

    async def _empty_history(
        agent_, chatroom_id, context_limit, provider, model, *, extra_projected_tokens=0, room=None
    ):
        return []

    engine._assemble_history = _empty_history  # type: ignore[attr-defined]

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="every_n_messages",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "skipped"
    assert result.reason == "no_input"
    room_events = [e for e in _PublisherSpy.emitted if e[0].startswith("ws:room:")]
    assert room_events == []
    user_events = [e for e in _PublisherSpy.emitted if e[0] == f"ws:user:{creator}"]
    assert [e[1] for e in user_events] == ["observation.started", "observation.skipped"]
    assert user_events[-1][2]["kind"] == "no_input"


@pytest.mark.asyncio
async def test_observer_turn_empty_reply_emits_observation_skipped(monkeypatch) -> None:
    """O-4 (P-2): benign skips emit observation.skipped, not observation.failed."""
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, _recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)

    async def _blank_stream(**kw):
        return te.ToolLoopOutcome(text="   ", rounds=1)

    engine._stream_with_tools = _blank_stream  # type: ignore[attr-defined]

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="every_n_messages",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "skipped"
    assert result.reason == "empty_reply"
    room_events = [e for e in _PublisherSpy.emitted if e[0].startswith("ws:room:")]
    assert room_events == []
    user_events = [e for e in _PublisherSpy.emitted if e[0] == f"ws:user:{creator}"]
    assert [e[1] for e in user_events] == ["observation.started", "observation.skipped"]
    assert user_events[-1][2]["kind"] == "empty_reply"


@pytest.mark.asyncio
async def test_an_empty_failed_synthesis_is_not_filed_as_a_benign_skip(monkeypatch) -> None:
    """AC-7. A provider outage that leaves nothing to say is not the model
    choosing silence: recorded as `empty_reply` it was indistinguishable from
    one, and the creator's UI showed a clean skip."""
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, _recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)

    async def _failed_stream(**kw):
        return te.ToolLoopOutcome(
            text="", rounds=8, synthesis_failed=True, error_kind="provider_exhausted:no_usable_key"
        )

    engine._stream_with_tools = _failed_stream  # type: ignore[attr-defined]

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="every_n_messages",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "skipped"
    assert result.reason == "provider_exhausted:no_usable_key"
    user_events = [e for e in _PublisherSpy.emitted if e[0] == f"ws:user:{creator}"]
    assert [e[1] for e in user_events] == ["observation.started", "observation.failed"]
    assert user_events[-1][2]["kind"] == "provider_exhausted:no_usable_key"


@pytest.mark.asyncio
async def test_a_failed_synthesis_marks_the_observation_it_persists(monkeypatch) -> None:
    """AC-7. The filler is kept — eight rounds of tool work stand behind it —
    but the stored observation says so."""
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)

    async def _failed_stream(**kw):
        return te.ToolLoopOutcome(
            text="Let me check that for you.",
            rounds=8,
            synthesis_failed=True,
            error_kind="provider_stream_failed",
        )

    engine._stream_with_tools = _failed_stream  # type: ignore[attr-defined]

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="every_n_messages",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "completed"
    assert recorded["content_md"] == "Let me check that for you."
    assert recorded["metadata"]["synthesis_failed"] is True
    assert recorded["metadata"]["synthesis_error"] == "provider_stream_failed"


# --------------------------------------------------------------------------- #
# Presentation blocks on the turn ([R28.15], [R28.16]) — AC-1, AC-3, AC-16, AC-17
# --------------------------------------------------------------------------- #

_BLOCKS = [
    {"kind": "prose", "text": "the room split three ways"},
    {
        "kind": "field_coverage",
        "basis": "server_facts",
        "type_key": "mandala-9grid",
        "submissions_counted": 12,
        "cells": [{"name": "home", "title": "Home", "filled": 9}],
    },
]


def _call_the_tool(engine, blocks=_BLOCKS) -> dict:
    """Stand in for the model calling `present_observation` mid-turn.

    What the engine sees afterwards is a filled sink, however it got there — the
    same shape ``test_activity_control_tools`` uses for the activation sink. The
    returned dict captures the kwargs so a test can assert `is_observer` was
    threaded rather than re-read.
    """
    seen: dict = {}

    async def _tools(agent_, agent_tools, **kw):
        seen.update(kw)
        sink = kw.get("observation_block_sink")
        if sink is not None and blocks is not None:
            sink.extend(blocks)
        return []

    engine._builtin_tools = _tools  # type: ignore[attr-defined]
    return seen


@pytest.mark.asyncio
async def test_an_observer_turn_records_its_blocks_and_their_serialisation(monkeypatch) -> None:
    """AC-1. The blocks reach the column, `content_md` is their markdown, and
    nothing is written to `messages`."""
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)
    seen = _call_the_tool(engine)

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="silence_minutes",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "completed"
    assert result.message_id is None
    assert recorded["blocks"] == _BLOCKS
    # The serialisation, not the model's closing text: the tool tells it the
    # blocks are what the teacher reads.
    assert "the room split three ways" in recorded["content_md"]
    assert "| Home | 9 |" in recorded["content_md"]
    assert "12 submissions counted." in recorded["content_md"]
    assert "private analysis" not in recorded["content_md"]
    assert [e for e in _PublisherSpy.emitted if e[0].startswith("ws:room:")] == []
    # AC-2's engine half: the flag is threaded from the role `run_turn` resolved,
    # never re-read inside the assembly.
    assert seen["is_observer"] is True


@pytest.mark.asyncio
async def test_an_observer_turn_that_never_calls_the_tool_is_byte_identical_to_before(
    monkeypatch,
) -> None:
    """AC-3."""
    agent = _observer_agent()
    engine, recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=uuid.uuid4())

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="silence_minutes",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "completed"
    assert recorded["content_md"] == "private analysis"
    # `None` and `[]` both persist as an empty array; what matters is that the
    # record carries no blocks.
    assert not recorded["blocks"]


@pytest.mark.asyncio
async def test_blocks_with_no_prose_are_recorded_rather_than_skipped(monkeypatch) -> None:
    """AC-16, and the correction §5.5 of the dossier exists for.

    A model told to deliver its analysis as blocks, that calls the tool and then
    says nothing, is the ordinary shape of this feature. Under a text-only empty
    guard every block would be discarded before the observer branch ran, and the
    creator would see `observation.skipped`.
    """
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)
    _call_the_tool(engine)

    async def _silent_stream(**kw):
        return te.ToolLoopOutcome(text="   ", rounds=2)

    engine._stream_with_tools = _silent_stream  # type: ignore[attr-defined]

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="silence_minutes",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "completed"
    assert recorded["blocks"] == _BLOCKS
    assert recorded["content_md"].strip()
    user_events = [e[1] for e in _PublisherSpy.emitted if e[0] == f"ws:user:{creator}"]
    assert user_events == ["observation.started", "observation.created"]


@pytest.mark.asyncio
async def test_blocks_survive_a_failed_synthesis_and_are_marked_not_filed_as_empty(
    monkeypatch,
) -> None:
    """AC-17. The tool rounds behind those blocks are real work, and the missing
    prose is a provider fault — filing it as `empty_reply` is the misfiling the
    non-empty path was already written to prevent."""
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)
    _call_the_tool(engine)

    async def _failed_stream(**kw):
        return te.ToolLoopOutcome(
            text="", rounds=8, synthesis_failed=True, error_kind="provider_stream_failed"
        )

    engine._stream_with_tools = _failed_stream  # type: ignore[attr-defined]

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="silence_minutes",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "completed"
    assert recorded["blocks"] == _BLOCKS
    assert recorded["metadata"]["synthesis_failed"] is True
    assert recorded["metadata"]["synthesis_error"] == "provider_stream_failed"
    user_events = [e[1] for e in _PublisherSpy.emitted if e[0] == f"ws:user:{creator}"]
    assert "observation.failed" not in user_events
    assert user_events[-1] == "observation.created"


def test_a_multi_line_observation_stays_one_memory_entry() -> None:
    """Self-audit ([R28.05]). A body is a whole markdown document once a turn
    delivers blocks, and the memory block is one entry per observation. Flat, a
    body's own `- ` lines read as new entries and its headings land at the top
    level of the system prompt."""
    entry = te._memory_entry("2026-08-24T10:00", "### Three things\n\n- one\n- two")

    lines = entry.splitlines()
    assert lines[0] == "- (2026-08-24T10:00) ### Three things"
    # Everything else is indented under it, so no continuation line can be read
    # as the start of another observation.
    assert all(line.startswith("  ") or not line.strip() for line in lines[1:])
    assert [line for line in lines if line.startswith("- (")] == [lines[0]]


def test_a_single_line_observation_is_unchanged() -> None:
    assert te._memory_entry("t", "just words") == "- (t) just words"


@pytest.mark.asyncio
async def test_blocks_that_render_to_nothing_are_still_an_empty_turn(monkeypatch) -> None:
    """Self-audit. The guard tests the serialisation, not the sink, so "never
    persist an empty message" holds by checking rather than by assuming. The tool
    refuses such an array on its own; this is what makes the engine safe anyway."""
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)
    _call_the_tool(engine, blocks=[{"kind": "prose", "text": "   "}])

    async def _silent_stream(**kw):
        return te.ToolLoopOutcome(text="", rounds=1)

    engine._stream_with_tools = _silent_stream  # type: ignore[attr-defined]

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="silence_minutes",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "skipped"
    assert recorded == {}


@pytest.mark.asyncio
async def test_neither_text_nor_blocks_is_still_a_skip(monkeypatch) -> None:
    """AC-17's other half. The guard was widened, not removed."""
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)
    _call_the_tool(engine, blocks=None)

    async def _silent_stream(**kw):
        return te.ToolLoopOutcome(text="", rounds=1)

    engine._stream_with_tools = _silent_stream  # type: ignore[attr-defined]

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="silence_minutes",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "skipped"
    assert result.reason == "empty_reply"
    assert recorded == {}
    user_events = [e[1] for e in _PublisherSpy.emitted if e[0] == f"ws:user:{creator}"]
    assert user_events == ["observation.started", "observation.skipped"]


@pytest.mark.asyncio
async def test_empty_reply_settles_pending_approvals(monkeypatch) -> None:
    """/code-review, FU-7 — the empty_reply skip used to return without
    restoring anything it drained, silently destroying any approval ballot
    the turn rendered but never voted on, even though the provider was
    reached and the model saw the note in its context."""
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, _recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)

    notes = [{"kind": "approval_request", "approval_id": str(uuid.uuid4()), "mode": "single"}]
    voted: set[uuid.UUID] = set()

    async def _pending(agent_, chatroom_id_):
        return None, [], notes, voted

    engine._pending_context_and_tools = _pending  # type: ignore[attr-defined]
    settled: list = []

    async def _settle(agent_, pending_notes, voted_):
        settled.append((agent_, pending_notes, voted_))

    engine._settle_pending_approvals = _settle  # type: ignore[attr-defined]

    async def _blank_stream(**kw):
        return te.ToolLoopOutcome(text="   ", rounds=1)

    engine._stream_with_tools = _blank_stream  # type: ignore[attr-defined]

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="every_n_messages",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "skipped"
    assert result.reason == "empty_reply"
    assert result.approvals_voted == 0
    assert settled == [(agent, notes, voted)]


@pytest.mark.asyncio
async def test_observer_turn_hard_failure_still_emits_observation_failed(monkeypatch) -> None:
    """O-4 (P-2) contrast pin: real errors keep the observation.failed event."""
    agent = _observer_agent()
    creator = uuid.uuid4()
    engine, _recorded, _stream_seen = _wire_observer_engine(monkeypatch, agent, creator_id=creator)

    async def _boom_stream(**kw):
        raise RuntimeError("provider exploded")

    engine._stream_with_tools = _boom_stream  # type: ignore[attr-defined]

    result = await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=uuid.uuid4(),
        trigger="every_n_messages",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )

    assert result.status == "failed"
    room_events = [e for e in _PublisherSpy.emitted if e[0].startswith("ws:room:")]
    assert room_events == []
    user_events = [e for e in _PublisherSpy.emitted if e[0] == f"ws:user:{creator}"]
    assert [e[1] for e in user_events] == ["observation.started", "observation.failed"]


# --------------------------------------------------------------------------- #
# pending_notify renderer branch (R28.07)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pending_context_renders_released_observation(monkeypatch) -> None:
    room_id = uuid.uuid4()
    notes = [{"kind": "released_observation", "chatroom_id": str(room_id), "content": "the brief"}]

    async def _drain(agent_id):
        return notes

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.drain", _drain)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = object()  # type: ignore[attr-defined]
    block, tools, drained, _voted = await engine._pending_context_and_tools(_observer_agent(), room_id)

    assert block is not None
    assert "The room owner shared an analysis with you:" in block
    assert "the brief" in block
    assert tools == []
    assert drained == notes


@pytest.mark.asyncio
async def test_pending_context_requeues_released_observation_for_a_different_room(monkeypatch) -> None:
    """R28.07 leak fix — pending_notify is keyed only by agent id, not room, so a
    note released into room A must never render into a turn running in room B."""
    room_a, room_b = uuid.uuid4(), uuid.uuid4()
    notes = [{"kind": "released_observation", "chatroom_id": str(room_a), "content": "room A's secret"}]

    async def _drain(agent_id):
        return notes

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.drain", _drain)
    requeued: list[tuple[uuid.UUID, list]] = []

    async def _requeue(agent_id, requeued_notes):
        requeued.append((agent_id, requeued_notes))

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.requeue", _requeue)

    agent = _observer_agent()
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = object()  # type: ignore[attr-defined]
    block, tools, drained, _voted = await engine._pending_context_and_tools(agent, room_b)

    assert block is None
    assert tools == []
    assert drained == []
    # Put back for room A's next turn — never rendered, never dropped.
    assert requeued == [(agent.id, notes)]


@pytest.mark.asyncio
async def test_pending_context_requeues_released_observation_for_headless_turn(monkeypatch) -> None:
    """A headless A2A turn has no room context at all, so any released
    observation note must be put back rather than rendered."""
    notes = [{"kind": "released_observation", "chatroom_id": str(uuid.uuid4()), "content": "secret"}]

    async def _drain(agent_id):
        return notes

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.drain", _drain)
    requeued: list[tuple[uuid.UUID, list]] = []

    async def _requeue(agent_id, requeued_notes):
        requeued.append((agent_id, requeued_notes))

    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.requeue", _requeue)

    agent = _observer_agent()
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = object()  # type: ignore[attr-defined]
    block, _tools, drained, _voted = await engine._pending_context_and_tools(agent, None)

    assert block is None
    assert drained == []
    assert requeued == [(agent.id, notes)]


# --------------------------------------------------------------------------- #
# best-effort helpers must not roll back the whole turn transaction
# --------------------------------------------------------------------------- #


class _RollbackAssertingDB(_FakeDB):
    def __init__(self) -> None:
        self.rollback_called = False

    async def rollback(self) -> None:
        self.rollback_called = True


@pytest.mark.asyncio
async def test_observer_memory_block_failure_does_not_rollback_whole_transaction(monkeypatch) -> None:
    """A DB hiccup fetching self-memory must not wipe the turn's already-
    pending agent.turn_started audit insert — only the SAVEPOINT rolls back,
    never self._db.rollback()."""

    class _BoomRepo:
        def __init__(self, db) -> None:
            pass

        async def list_recent_for_agent(self, **kw):
            raise RuntimeError("transient db error")

    monkeypatch.setattr(te, "ObservationRepository", _BoomRepo)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    db = _RollbackAssertingDB()
    engine._db = db  # type: ignore[attr-defined]

    result = await engine._observer_memory_block(_observer_agent(), uuid.uuid4())

    assert result is None
    assert db.rollback_called is False


@pytest.mark.asyncio
async def test_emit_observation_event_recipient_lookup_failure_does_not_rollback(monkeypatch) -> None:
    class _BoomObsService:
        def __init__(self, db) -> None:
            pass

        async def recipient_user_id(self, chatroom_id):
            raise RuntimeError("transient db error")

    monkeypatch.setattr(te, "ObservationService", _BoomObsService)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    db = _RollbackAssertingDB()
    engine._db = db  # type: ignore[attr-defined]

    await engine._emit_observation_event(uuid.uuid4(), uuid.uuid4(), "observation.failed", {"kind": "x"})

    assert db.rollback_called is False


@pytest.mark.asyncio
async def test_observer_memory_block_succeeds_normally(monkeypatch) -> None:
    """Confirms the SAVEPOINT wrapping doesn't break the happy path."""

    class _OkRepo:
        def __init__(self, db) -> None:
            pass

        async def list_recent_for_agent(self, **kw):
            return [SimpleNamespace(created_at=None, content_md="earlier note")]

    monkeypatch.setattr(te, "ObservationRepository", _OkRepo)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]

    result = await engine._observer_memory_block(_observer_agent(), uuid.uuid4())

    assert result is not None
    assert "earlier note" in result


# --------------------------------------------------------------------------- #
# ObservationRepository.list — before-cursor cross-room scoping
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_observation_list_before_anchor_scoped_by_chatroom_id() -> None:
    """The before-cursor anchor lookup must not resolve a different room's
    observation id; it must be scoped by chatroom_id exactly like the page
    query is — otherwise a creator could page room A using the timestamp of
    an observation id borrowed from room B."""
    room_id = uuid.uuid4()
    before_id = uuid.uuid4()

    db = AsyncMock()
    anchor_result = MagicMock()
    anchor_result.first.return_value = SimpleNamespace(
        created_at=datetime(2026, 1, 1, tzinfo=UTC), id=before_id
    )
    page_result = MagicMock()
    page_result.all.return_value = []
    db.execute.side_effect = [anchor_result, page_result]

    repo = ObservationRepository(db)
    await repo.list(chatroom_id=room_id, before=before_id, limit=10)

    anchor_stmt = db.execute.await_args_list[0].args[0]
    compiled = str(anchor_stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert str(room_id) in compiled
    assert str(before_id) in compiled


# --------------------------------------------------------------------------- #
# ObservationRepository — the blocks column ([R28.15])
# --------------------------------------------------------------------------- #


def _create_returning(**overrides):
    """A fake INSERT ... RETURNING row for `_row_to_observation`."""
    row = {
        "id": uuid.uuid4(),
        "chatroom_id": uuid.uuid4(),
        "agent_id": uuid.uuid4(),
        "content_md": "serialised",
        "metadata": {},
        "blocks": [],
        "trigger": "silence_minutes",
        "trigger_message_id": None,
        "released_at": None,
        "release_target": None,
        "released_by_user_id": None,
        "created_at": None,
        "deleted_at": None,
    }
    row.update(overrides)
    return SimpleNamespace(**row)


async def _create_with(db_row, **kwargs):
    db = AsyncMock()
    result = MagicMock()
    result.one.return_value = db_row
    db.execute.return_value = result
    observation = await ObservationRepository(db).create(
        chatroom_id=db_row.chatroom_id,
        agent_id=db_row.agent_id,
        content_md=db_row.content_md,
        trigger=db_row.trigger,
        **kwargs,
    )
    stmt = db.execute.await_args_list[0].args[0]
    return observation, stmt


@pytest.mark.asyncio
async def test_create_persists_and_maps_blocks() -> None:
    blocks = [{"kind": "prose", "text": "hello"}]
    observation, stmt = await _create_with(_create_returning(blocks=blocks), blocks=blocks)
    assert observation.blocks == blocks
    assert stmt.compile().params["blocks"] == blocks


@pytest.mark.asyncio
async def test_create_without_blocks_writes_an_empty_array() -> None:
    """AC-3: a turn that never called the tool stores `[]`, not NULL — the column
    is NOT NULL and every reader treats an empty array as "render content_md"."""
    _, stmt = await _create_with(_create_returning())
    assert stmt.compile().params["blocks"] == []


def test_row_to_observation_tolerates_a_null_blocks_column() -> None:
    """Rows written before 0080 read back as no blocks rather than as None.

    The column is NOT NULL going forward, but a repository mapper that would raise
    on a legacy NULL turns a schema rollback into an unreadable panel.
    """
    from contexts.conversation.infrastructure.repositories.observation_repo import _row_to_observation

    assert _row_to_observation(_create_returning(blocks=None)).blocks == []
