"""Room wake-up routes: who may read and change them ([R15.25], matrix row 29).

Phase 2 AC-11/AC-12: a current room creator manages any binding, a project
editor a normal one; an observer binding is indistinguishable from an unbound
agent to everyone else, and a plain member or guest cannot manage either.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.api.v1.room_wakeup as mod
from contexts.conversation.application.access import RoomAccess, can_manage_room_wakeup
from contexts.conversation.domain.models import BindingWakeupState, ChatroomAgentRole
from contexts.orchestration.application.room_wakeup_service import (
    RoomWakeupChange,
    TimerEffects,
    build_room_wakeup,
)
from shared_kernel.auth.permissions import Principal, Role

_CONFIG = {
    "triggers": {"every_n_messages": {"enabled": True, "n": 3}, "silence_minutes": {"enabled": True}},
    "self_toggle": ["silence_minutes"],
}


def _principal(user_id=None, *, is_admin=False) -> Principal:
    return Principal(user_id=user_id or uuid.uuid4(), is_admin=is_admin, email_verified=True)


def _access(*, created_by=None, roles=frozenset(), is_guest=False) -> RoomAccess:
    room = SimpleNamespace(
        created_by_user_id=created_by,
        allow_project_owners_only=False,
        allow_project_members=True,
        allow_member_groups=False,
        allow_org_members=False,
        allow_guest_links=True,
    )
    return RoomAccess(chatroom=room, project_id=uuid.uuid4(), roles=roles, is_guest=is_guest)


def _current(role: ChatroomAgentRole = ChatroomAgentRole.NORMAL):
    agent = SimpleNamespace(id=uuid.uuid4(), deleted_at=None, wakeup_config=_CONFIG)
    state = BindingWakeupState(
        chatroom_id=uuid.uuid4(),
        agent_id=agent.id,
        binding_id=uuid.uuid4(),
        role=role,
        version=4,
        room_overrides={"every_n_messages": 5},
        runtime_overrides={"silence_minutes": 9},
        runtime_started_at=None,
    )
    return build_room_wakeup(state, agent)


def _wire(monkeypatch, *, access: RoomAccess, current) -> SimpleNamespace:
    calls = SimpleNamespace(patched=[], reset=[], loaded=0, applied=[], apply_error=None)
    effects = (
        TimerEffects(
            agent_id=current.state.agent_id,
            room_id=current.state.chatroom_id,
            binding_id=current.state.binding_id,
            rearm_silence=True,
        )
        if current is not None
        else None
    )

    async def _resolve(db, *, principal, chatroom_id):
        return access

    class _Conv:
        def __init__(self, db) -> None:
            pass

        async def binding_wakeup_state(self, *, chatroom_id, agent_id):
            return current.state if current is not None else None

    class _Orch:
        def __init__(self, db) -> None:
            pass

        async def room_wakeup_for_state(self, state):
            calls.loaded += 1
            return current

        async def patch_room_wakeup(self, **kw):
            calls.patched.append(kw)
            return RoomWakeupChange(wakeup=current, effects=effects)

        async def reset_room_wakeup(self, **kw):
            calls.reset.append(kw)
            return RoomWakeupChange(wakeup=current, effects=effects)

        @staticmethod
        async def apply_room_wakeup_effects(applied):
            if calls.apply_error is not None:
                raise calls.apply_error
            calls.applied.append(list(applied))

    monkeypatch.setattr(mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(mod, "OrchestrationFacade", _Orch)
    monkeypatch.setattr(mod, "ConversationFacade", _Conv)
    return calls


async def _get(principal):
    return await mod.get_room_wakeup(
        chatroom_id=uuid.uuid4(), agent_id=uuid.uuid4(), principal=principal, db=SimpleNamespace()
    )


class TestWhoMayManage:
    @pytest.mark.parametrize(
        ("roles", "observer", "allowed"),
        [
            (frozenset({Role.PROJECT_OWNER}), False, True),
            (frozenset({Role.PROJECT_OWNER}), True, False),
            (frozenset({Role.PROJECT_MEMBER}), False, False),
            (frozenset({Role.ORG_MEMBER}), False, False),
            (frozenset(), False, False),
        ],
    )
    def test_a_non_creator(self, roles, observer, allowed) -> None:
        access = _access(created_by=uuid.uuid4(), roles=roles)

        assert can_manage_room_wakeup(access, principal=_principal(), observer_binding=observer) is allowed

    @pytest.mark.parametrize("observer", [False, True])
    def test_the_creator_and_admin_manage_any_binding(self, observer) -> None:
        uid = uuid.uuid4()
        member_creator = _access(created_by=uid, roles=frozenset({Role.PROJECT_MEMBER}))

        assert can_manage_room_wakeup(member_creator, principal=_principal(uid), observer_binding=observer)
        assert can_manage_room_wakeup(
            _access(created_by=uuid.uuid4()), principal=_principal(is_admin=True), observer_binding=observer
        )

    def test_a_guest_never_manages(self) -> None:
        access = _access(created_by=uuid.uuid4(), is_guest=True)

        assert not can_manage_room_wakeup(access, principal=_principal(), observer_binding=False)


class TestRoutes:
    async def test_the_creator_reads_values_sources_and_no_agent_config(self, monkeypatch) -> None:
        uid = uuid.uuid4()
        current = _current()
        _wire(
            monkeypatch,
            access=_access(created_by=uid, roles=frozenset({Role.PROJECT_MEMBER})),
            current=current,
        )

        out = await _get(_principal(uid))

        assert (out.every_n_messages.value, out.every_n_messages.source) == (5, "room")
        assert out.every_n_messages.designer_value == 3
        assert (out.silence_minutes.value, out.silence_minutes.source) == (9, "runtime")
        assert out.binding_id == current.state.binding_id
        assert out.version == 4
        assert out.agent_may_toggle == ["silence_minutes"]
        dumped = out.model_dump()
        assert not {"system_prompt", "tools", "skills", "key_group_id", "wakeup_config"} & dumped.keys()

    async def test_a_hidden_observer_reads_as_unbound(self, monkeypatch) -> None:
        calls = _wire(
            monkeypatch,
            access=_access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER})),
            current=_current(ChatroomAgentRole.OBSERVER),
        )

        with pytest.raises(HTTPException) as exc:
            await _get(_principal())
        assert exc.value.status_code == 404
        # Refused from the binding row: the agent is never loaded, so the answer
        # costs what an unbound agent's does.
        assert calls.loaded == 0

    async def test_an_unbound_agent_is_404(self, monkeypatch) -> None:
        _wire(
            monkeypatch,
            access=_access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER})),
            current=None,
        )

        with pytest.raises(HTTPException) as exc:
            await _get(_principal())
        assert exc.value.status_code == 404

    async def test_a_plain_member_is_refused_a_normal_binding(self, monkeypatch) -> None:
        calls = _wire(
            monkeypatch,
            access=_access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_MEMBER})),
            current=_current(),
        )
        db = SimpleNamespace(commit=AsyncMock())

        with pytest.raises(HTTPException) as exc:
            await mod.patch_room_wakeup(
                body=mod.RoomWakeupPatchIn(binding_id=uuid.uuid4(), expected_version=1, every_n_messages=4),
                chatroom_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                principal=_principal(),
                db=db,
            )
        assert exc.value.status_code == 403
        assert calls.patched == []
        db.commit.assert_not_awaited()

    async def test_an_editor_patches_with_the_callers_identity_and_version(self, monkeypatch) -> None:
        calls = _wire(
            monkeypatch,
            access=_access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER})),
            current=_current(),
        )
        db = SimpleNamespace(commit=AsyncMock())
        principal = _principal()
        binding = uuid.uuid4()

        await mod.patch_room_wakeup(
            body=mod.RoomWakeupPatchIn(binding_id=binding, expected_version=4, silence_minutes_enabled=False),
            chatroom_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            principal=principal,
            db=db,
        )

        (call,) = calls.patched
        assert (call["binding_id"], call["expected_version"], call["actor_user_id"]) == (
            binding,
            4,
            principal.user_id,
        )
        assert call["patch"].to_dict() == {"silence_minutes_enabled": False}
        db.commit.assert_awaited_once()
        # Code review #8: the write loads the binding and agent itself.
        assert calls.loaded == 0

    async def test_on_join_is_shown_with_its_source_for_a_normal_binding(self, monkeypatch) -> None:
        """Phase 3 AC-7."""
        uid = uuid.uuid4()
        _wire(
            monkeypatch,
            access=_access(created_by=uid, roles=frozenset({Role.PROJECT_MEMBER})),
            current=_current(),
        )

        out = await _get(_principal(uid))

        assert out.on_join_enabled is not None
        assert (out.on_join_enabled.value, out.on_join_enabled.source) == (False, "designer")

    async def test_on_join_is_absent_for_an_observer_binding(self, monkeypatch) -> None:
        """Phase 3 AC-7: the creator manages the observer, which cannot wake on arrivals."""
        uid = uuid.uuid4()
        _wire(
            monkeypatch,
            access=_access(created_by=uid, roles=frozenset({Role.PROJECT_MEMBER})),
            current=_current(ChatroomAgentRole.OBSERVER),
        )

        out = await _get(_principal(uid))

        assert out.on_join_enabled is None

    async def test_on_join_cannot_be_set_on_an_observer_binding(self, monkeypatch) -> None:
        uid = uuid.uuid4()
        calls = _wire(
            monkeypatch,
            access=_access(created_by=uid, roles=frozenset({Role.PROJECT_MEMBER})),
            current=_current(ChatroomAgentRole.OBSERVER),
        )

        with pytest.raises(HTTPException) as exc:
            await mod.patch_room_wakeup(
                body=mod.RoomWakeupPatchIn(binding_id=uuid.uuid4(), expected_version=4, on_join_enabled=True),
                chatroom_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                principal=_principal(uid),
                db=SimpleNamespace(commit=AsyncMock()),
            )
        assert exc.value.status_code == 422
        assert calls.patched == []

    async def test_on_join_is_patched_on_a_normal_binding(self, monkeypatch) -> None:
        uid = uuid.uuid4()
        calls = _wire(
            monkeypatch,
            access=_access(created_by=uid, roles=frozenset({Role.PROJECT_MEMBER})),
            current=_current(),
        )

        await mod.patch_room_wakeup(
            body=mod.RoomWakeupPatchIn(binding_id=uuid.uuid4(), expected_version=4, on_join_enabled=True),
            chatroom_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            principal=_principal(uid),
            db=SimpleNamespace(commit=AsyncMock()),
        )

        assert calls.patched[0]["patch"].to_dict() == {"on_join_enabled": True}

    async def test_reset_names_its_scope(self, monkeypatch) -> None:
        uid = uuid.uuid4()
        calls = _wire(
            monkeypatch,
            access=_access(created_by=uid, roles=frozenset({Role.PROJECT_MEMBER})),
            current=_current(),
        )

        await mod.reset_room_wakeup(
            body=mod.RoomWakeupResetIn(binding_id=uuid.uuid4(), expected_version=4, scope="runtime"),
            chatroom_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            principal=_principal(uid),
            db=SimpleNamespace(commit=AsyncMock()),
        )

        assert calls.reset[0]["scope"] == "runtime"

    async def _patch_as_creator(self, monkeypatch, db):
        uid = uuid.uuid4()
        calls = _wire(
            monkeypatch,
            access=_access(created_by=uid, roles=frozenset({Role.PROJECT_MEMBER})),
            current=_current(),
        )
        return calls, mod.patch_room_wakeup(
            body=mod.RoomWakeupPatchIn(
                binding_id=uuid.uuid4(), expected_version=4, silence_minutes_enabled=True
            ),
            chatroom_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            principal=_principal(uid),
            db=db,
        )

    async def test_timers_are_applied_only_after_the_commit(self, monkeypatch) -> None:
        """Phase 2 D-14: the write's timer effects follow its commit, never precede it."""
        order: list[str] = []
        calls_box: list[SimpleNamespace] = []

        async def _commit() -> None:
            order.append(f"commit (applied so far: {len(calls_box[0].applied)})")

        calls, pending = await self._patch_as_creator(monkeypatch, SimpleNamespace(commit=_commit))
        calls_box.append(calls)
        await pending

        assert order == ["commit (applied so far: 0)"]
        assert len(calls.applied) == 1
        assert calls.applied[0][0].rearm_silence

    async def test_a_failed_commit_applies_no_timer(self, monkeypatch) -> None:
        calls, pending = await self._patch_as_creator(
            monkeypatch, SimpleNamespace(commit=AsyncMock(side_effect=RuntimeError("commit failed")))
        )

        with pytest.raises(RuntimeError):
            await pending
        assert calls.applied == []

    async def test_a_timer_failure_after_the_commit_still_returns_the_saved_settings(
        self, monkeypatch
    ) -> None:
        calls, pending = await self._patch_as_creator(monkeypatch, SimpleNamespace(commit=AsyncMock()))
        calls.apply_error = RuntimeError("redis down")

        out = await pending

        assert out.version == 4
        assert calls.applied == []


class TestBodies:
    def test_an_empty_patch_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            mod.RoomWakeupPatchIn(binding_id=uuid.uuid4(), expected_version=1)

    @pytest.mark.parametrize(
        "field",
        [{"every_n_messages": 0}, {"silence_minutes": 1441}, {"autostop_rounds": 101}, {"count": "bots"}],
    )
    def test_out_of_bounds_values_are_rejected(self, field) -> None:
        with pytest.raises(ValidationError):
            mod.RoomWakeupPatchIn(binding_id=uuid.uuid4(), expected_version=1, **field)

    def test_the_designer_only_fields_are_not_accepted(self) -> None:
        body = mod.RoomWakeupPatchIn.model_validate(
            {
                "binding_id": str(uuid.uuid4()),
                "expected_version": 1,
                "every_n_messages": 4,
                "self_toggle": ["x"],
            }
        )

        assert body.overrides().to_dict() == {"every_n_messages": 4}
        assert not hasattr(body, "self_toggle")
