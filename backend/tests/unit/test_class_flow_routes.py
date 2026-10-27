"""Class-start and time-limit routes ([R15.27], [R30.46]; phase 4 AC-7)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.api.v1.class_flow as mod
from contexts.conversation.application.access import RoomAccess
from contexts.conversation.domain.errors import ForbiddenInRoom, NotRoomCreator
from contexts.conversation.domain.models import ClassStartConfig, ClassStartState
from shared_kernel.auth.permissions import Principal, Role

_CREATOR = uuid.uuid4()
_ROOM = uuid.uuid4()


def _principal(user_id: uuid.UUID | None = None) -> Principal:
    return Principal(user_id=user_id or uuid.uuid4(), is_admin=False, email_verified=True)


def _access(*, readable: bool = True) -> RoomAccess:
    room = SimpleNamespace(
        created_by_user_id=_CREATOR,
        allow_project_owners_only=False,
        allow_project_members=readable,
        allow_member_groups=False,
        allow_org_members=False,
        allow_guest_links=False,
    )
    roles = frozenset({Role.PROJECT_MEMBER}) if readable else frozenset()
    return RoomAccess(chatroom=room, project_id=uuid.uuid4(), roles=roles, is_guest=False)  # type: ignore[arg-type]


class _Db:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _wire(monkeypatch: pytest.MonkeyPatch, *, access: RoomAccess, config: ClassStartConfig | None = None):
    calls = SimpleNamespace(writes=[], limits=[], checked=[], stored={})
    state = ClassStartState(chatroom_id=_ROOM, config=config, first_arrival_at=None, started_at=None)

    async def _resolve(db: Any, *, principal: Principal, chatroom_id: uuid.UUID) -> RoomAccess:
        return access

    class _Conv:
        def __init__(self, db: Any) -> None:
            pass

        async def class_start_state(self, chatroom_id: uuid.UUID) -> ClassStartState:
            return state

        async def write_class_start(self, chatroom_id: uuid.UUID, **kw: Any) -> ClassStartState:
            calls.writes.append(kw)
            return ClassStartState(
                chatroom_id=chatroom_id,
                config=kw["config"],
                first_arrival_at=None,
                started_at=None if kw["reset"] else state.started_at,
            )

    class _Act:
        def __init__(self, db: Any) -> None:
            pass

        async def room_time_limits(self, chatroom_id: uuid.UUID) -> dict:
            return dict(calls.stored)

        async def replace_room_time_limits(self, chatroom_id: uuid.UUID, limits: dict, **kw: Any) -> None:
            calls.limits.append((limits, kw))

    async def _assert_types(db: Any, project_id: uuid.UUID, ids: list[uuid.UUID]) -> None:
        calls.checked.append((project_id, ids))

    monkeypatch.setattr(mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(mod, "ConversationFacade", _Conv)
    monkeypatch.setattr(mod, "ActivitiesFacade", _Act)
    monkeypatch.setattr(mod, "_assert_activity_types_in_project", _assert_types)
    return calls


_CTX = SimpleNamespace(actor_ip="203.0.113.9", request_id=None)


class TestAuthority:
    @pytest.mark.parametrize(
        "call",
        [
            lambda p, db: mod.get_class_start(chatroom_id=_ROOM, principal=p, db=db),
            lambda p, db: mod.patch_class_start(
                mod.ClassStartPatchIn(enabled=True, min_participants=3, max_wait_minutes=2),
                chatroom_id=_ROOM,
                ctx=_CTX,
                principal=p,
                db=db,
            ),
            lambda p, db: mod.get_activity_time_limits(chatroom_id=_ROOM, principal=p, db=db),
            lambda p, db: mod.put_activity_time_limits(
                mod.ActivityTimeLimitsBody(limits=[]), chatroom_id=_ROOM, ctx=_CTX, principal=p, db=db
            ),
        ],
    )
    async def test_a_reader_who_is_not_the_creator_is_refused(self, monkeypatch, call) -> None:
        calls = _wire(monkeypatch, access=_access())

        with pytest.raises(NotRoomCreator):
            await call(_principal(), _Db())
        assert calls.writes == []
        assert calls.limits == []

    async def test_a_caller_who_cannot_read_the_room_is_refused_before_the_creator_check(
        self, monkeypatch
    ) -> None:
        _wire(monkeypatch, access=_access(readable=False))

        with pytest.raises(ForbiddenInRoom):
            await mod.get_class_start(chatroom_id=_ROOM, principal=_principal(_CREATOR), db=_Db())


class TestClassStart:
    async def test_the_first_write_needs_every_field(self, monkeypatch) -> None:
        _wire(monkeypatch, access=_access())

        with pytest.raises(HTTPException) as exc:
            await mod.patch_class_start(
                mod.ClassStartPatchIn(enabled=True),
                chatroom_id=_ROOM,
                ctx=_CTX,
                principal=_principal(_CREATOR),
                db=_Db(),
            )
        assert exc.value.status_code == 422

    async def test_a_partial_patch_keeps_the_stored_fields_and_can_reset(self, monkeypatch) -> None:
        stored = ClassStartConfig(enabled=True, min_participants=3, max_wait_minutes=2)
        calls = _wire(monkeypatch, access=_access(), config=stored)
        db = _Db()

        out = await mod.patch_class_start(
            mod.ClassStartPatchIn(max_wait_minutes=5, reset=True),
            chatroom_id=_ROOM,
            ctx=_CTX,
            principal=_principal(_CREATOR),
            db=db,
        )

        (write,) = calls.writes
        assert write["config"] == ClassStartConfig(enabled=True, min_participants=3, max_wait_minutes=5)
        assert write["reset"] is True
        assert write["actor_user_id"] == _CREATOR
        assert (out.min_participants, out.max_wait_minutes, out.started_at) == (3, 5, None)
        assert db.commits == 1

    @pytest.mark.parametrize(
        "body",
        [
            {"min_participants": 0},
            {"min_participants": 101},
            {"max_wait_minutes": 0},
            {"max_wait_minutes": 61},
        ],
    )
    def test_bounds_are_validated_at_the_boundary(self, body: dict) -> None:
        with pytest.raises(ValidationError):
            mod.ClassStartPatchIn(**body)


class TestTimeLimits:
    async def test_every_type_id_is_resolved_for_the_rooms_project(self, monkeypatch) -> None:
        access = _access()
        calls = _wire(monkeypatch, access=access)
        a, b = uuid.uuid4(), uuid.uuid4()

        await mod.put_activity_time_limits(
            mod.ActivityTimeLimitsBody(
                limits=[
                    mod.ActivityTimeLimitItem(activity_type_id=a, time_limit_minutes=8),
                    mod.ActivityTimeLimitItem(activity_type_id=b, time_limit_minutes=None),
                ]
            ),
            chatroom_id=_ROOM,
            ctx=_CTX,
            principal=_principal(_CREATOR),
            db=_Db(),
        )

        assert calls.checked == [(access.project_id, [a, b])]
        ((limits, kw),) = calls.limits
        assert limits == {a: 8, b: None}
        assert kw["actor_user_id"] == _CREATOR

    async def test_a_repeated_type_is_refused(self, monkeypatch) -> None:
        calls = _wire(monkeypatch, access=_access())
        a = uuid.uuid4()

        with pytest.raises(HTTPException) as exc:
            await mod.put_activity_time_limits(
                mod.ActivityTimeLimitsBody(
                    limits=[
                        mod.ActivityTimeLimitItem(activity_type_id=a, time_limit_minutes=8),
                        mod.ActivityTimeLimitItem(activity_type_id=a, time_limit_minutes=9),
                    ]
                ),
                chatroom_id=_ROOM,
                ctx=_CTX,
                principal=_principal(_CREATOR),
                db=_Db(),
            )
        assert exc.value.status_code == 422
        assert calls.limits == []

    @pytest.mark.parametrize("minutes", [0, 181])
    def test_bounds_are_validated_at_the_boundary(self, minutes: int) -> None:
        with pytest.raises(ValidationError):
            mod.ActivityTimeLimitItem(activity_type_id=uuid.uuid4(), time_limit_minutes=minutes)

    def test_the_body_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            mod.ActivityTimeLimitsBody(
                limits=[{"activity_type_id": uuid.uuid4(), "time_limit_minutes": 5} for _ in range(201)]  # type: ignore[misc]
            )
