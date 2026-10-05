"""Canvas template readability across tenants -- audit F-12.

Dossier ``docs/tasks/2026-10-05-canvas-template-tenant-scope``. [R13.59]: platform
templates are readable by every authenticated principal, project templates by the
project's members only. The list leaked every project's templates for
``scope=project`` without ``project_id``; apply copied any template, from any
project, into any room the caller could write. Every refusal of a template the
caller may not read must be indistinguishable from an unknown id.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

import app.api.v1.canvas_templates as route
import shared_kernel.auth.dependencies as auth_deps
from contexts.canvas.domain.models import CanvasTemplate, CanvasTemplateScope
from contexts.conversation.application.access import RoomAccess
from shared_kernel.auth.permissions import Principal, Role
from tests.unit.chatroom_fakes import chatroom_row

_PROJECT = uuid.uuid4()
_FOREIGN_PROJECT = uuid.uuid4()
_NOT_FOUND = "Template not found"


def _template(scope: CanvasTemplateScope, project_id: uuid.UUID | None = None) -> CanvasTemplate:
    return CanvasTemplate(
        id=uuid.uuid4(),
        scope=scope,
        project_id=project_id,
        name="t",
        description=None,
        template_data={"elements": []},
        created_by_user_id=None,
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
    )


_PLATFORM = _template(CanvasTemplateScope.PLATFORM)
_OWN = _template(CanvasTemplateScope.PROJECT, _PROJECT)
_FOREIGN = _template(CanvasTemplateScope.PROJECT, _FOREIGN_PROJECT)


def _member() -> Principal:
    return Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True)


def _guest(room_id: uuid.UUID) -> Principal:
    return Principal(
        user_id=uuid.uuid4(), is_admin=False, email_verified=False, is_guest=True, chatroom_id=room_id
    )


class _Facade:
    """Stand-in for `CanvasFacade`: the route's decisions are under test, not storage."""

    applied: list[uuid.UUID]
    deleted: list[uuid.UUID]
    listed: list[dict[str, Any]]

    def __init__(self, db: object, **_: object) -> None:
        pass

    async def get_template(self, template_id: uuid.UUID) -> CanvasTemplate | None:
        return next((t for t in (_PLATFORM, _OWN, _FOREIGN) if t.id == template_id), None)

    async def list_templates(self, **kwargs: Any) -> list[CanvasTemplate]:
        type(self).listed.append(kwargs)
        return []

    async def get_or_create(self, *, chatroom_id: uuid.UUID) -> SimpleNamespace:
        return SimpleNamespace(id=uuid.uuid4())

    async def apply_template(self, **kwargs: Any) -> dict[str, Any]:
        if kwargs["template_id"] not in {t.id for t in (_PLATFORM, _OWN, _FOREIGN)}:
            raise ValueError(_NOT_FOUND)
        type(self).applied.append(kwargs["template_id"])
        return {"created": [], "updated": [], "deleted": 0}

    async def delete_template(self, template_id: uuid.UUID, **_: Any) -> bool:
        type(self).deleted.append(template_id)
        return True


class _Db:
    async def commit(self) -> None:
        return None


_MEMBERS: set[uuid.UUID] = set()


class _Resolver:
    """A principal holds MEMBER in `_PROJECT` iff `_join` added it; guests hold nothing."""

    async def roles_for(self, principal: Principal, scope: Any) -> frozenset[Role]:
        if not principal.is_guest and scope.project_id == _PROJECT and principal.user_id in _MEMBERS:
            return frozenset({Role.PROJECT_MEMBER})
        return frozenset()


async def _get_resolver(db: object = None) -> _Resolver:
    return _Resolver()


@pytest.fixture(autouse=True)
def wired(monkeypatch: pytest.MonkeyPatch) -> None:
    _Facade.applied, _Facade.deleted, _Facade.listed = [], [], []
    _MEMBERS.clear()
    monkeypatch.setattr(route, "CanvasFacade", _Facade)
    monkeypatch.setattr(route, "TenancyRoleResolver", lambda db: _Resolver())
    monkeypatch.setattr(route, "get_role_resolver", _get_resolver)
    monkeypatch.setattr(auth_deps, "get_role_resolver", _get_resolver)


def _join(principal: Principal) -> Principal:
    _MEMBERS.add(principal.user_id)
    return principal


def _room_access(monkeypatch: pytest.MonkeyPatch, *, roles: frozenset[Role], is_guest: bool = False) -> Any:
    room = chatroom_row()
    access = RoomAccess(chatroom=room, project_id=_PROJECT, roles=roles, is_guest=is_guest)

    async def _resolve(db: object, *, principal: Principal, chatroom_id: uuid.UUID) -> RoomAccess:
        return access

    monkeypatch.setattr(route, "resolve_room_access", _resolve)
    return room


def _status(exc: pytest.ExceptionInfo[BaseException]) -> int:
    value = exc.value
    return int(getattr(value, "status_code", None) or getattr(value, "status", 0))


class TestList:
    async def test_project_scope_without_project_id_is_rejected(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await route.list_templates(scope="project", project_id=None, principal=_member(), db=_Db())
        assert exc.value.status_code == 422
        assert _Facade.listed == []

    async def test_a_foreign_project_id_is_refused(self) -> None:
        with pytest.raises(Exception) as exc:
            await route.list_templates(
                scope="project", project_id=_FOREIGN_PROJECT, principal=_join(_member()), db=_Db()
            )
        assert _status(exc) == 403
        assert _Facade.listed == []

    @pytest.mark.parametrize("scope", [None, "platform"])
    async def test_a_foreign_project_id_is_refused_for_any_scope(self, scope: str | None) -> None:
        with pytest.raises(Exception) as exc:
            await route.list_templates(
                scope=scope, project_id=_FOREIGN_PROJECT, principal=_join(_member()), db=_Db()
            )
        assert _status(exc) == 403
        assert _Facade.listed == []

    async def test_a_guest_cannot_list_project_templates(self) -> None:
        with pytest.raises(Exception) as exc:
            await route.list_templates(
                scope="project", project_id=_PROJECT, principal=_guest(uuid.uuid4()), db=_Db()
            )
        assert _status(exc) == 403

    async def test_a_member_lists_their_project(self) -> None:
        await route.list_templates(scope="project", project_id=_PROJECT, principal=_join(_member()), db=_Db())
        assert _Facade.listed == [{"scope": CanvasTemplateScope.PROJECT, "project_id": _PROJECT}]


class TestApply:
    async def _apply(self, principal: Principal, room: Any, template_id: uuid.UUID) -> dict[str, Any]:
        return await route.apply_template(
            chatroom_id=room.id,
            body=route.ApplyTemplateIn(template_id=template_id),
            principal=principal,
            db=_Db(),
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
        )

    async def test_a_foreign_projects_template_answers_like_an_unknown_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        room = _room_access(monkeypatch, roles=frozenset({Role.PROJECT_MEMBER}))
        member = _join(_member())

        with pytest.raises(HTTPException) as foreign:
            await self._apply(member, room, _FOREIGN.id)
        with pytest.raises(HTTPException) as unknown:
            await self._apply(member, room, uuid.uuid4())

        assert (foreign.value.status_code, foreign.value.detail) == (404, _NOT_FOUND)
        assert (unknown.value.status_code, unknown.value.detail) == (404, _NOT_FOUND)
        assert _Facade.applied == []

    async def test_a_member_applies_their_projects_template(self, monkeypatch: pytest.MonkeyPatch) -> None:
        room = _room_access(monkeypatch, roles=frozenset({Role.PROJECT_MEMBER}))
        await self._apply(_join(_member()), room, _OWN.id)
        assert _Facade.applied == [_OWN.id]

    async def test_a_guest_applies_a_platform_template(self, monkeypatch: pytest.MonkeyPatch) -> None:
        room = _room_access(monkeypatch, roles=frozenset(), is_guest=True)
        await self._apply(_guest(room.id), room, _PLATFORM.id)
        assert _Facade.applied == [_PLATFORM.id]

    async def test_a_guest_cannot_apply_the_rooms_own_project_template(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        room = _room_access(monkeypatch, roles=frozenset(), is_guest=True)
        with pytest.raises(HTTPException) as exc:
            await self._apply(_guest(room.id), room, _OWN.id)
        assert (exc.value.status_code, exc.value.detail) == (404, _NOT_FOUND)
        assert _Facade.applied == []


class TestReadAndDelete:
    async def test_get_of_a_foreign_template_answers_like_an_unknown_id(self) -> None:
        member = _join(_member())
        with pytest.raises(HTTPException) as foreign:
            await route.get_template(_FOREIGN.id, principal=member, db=_Db())
        with pytest.raises(HTTPException) as unknown:
            await route.get_template(uuid.uuid4(), principal=member, db=_Db())
        assert (foreign.value.status_code, foreign.value.detail) == (404, _NOT_FOUND)
        assert (unknown.value.status_code, unknown.value.detail) == (404, _NOT_FOUND)

    async def test_delete_of_a_foreign_template_is_404(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await route.delete_template(
                _FOREIGN.id,
                principal=_join(_member()),
                resolver=await auth_deps.get_role_resolver(None),
                db=_Db(),
                ctx=SimpleNamespace(actor_ip=None, request_id=None),
            )
        assert (exc.value.status_code, exc.value.detail) == (404, _NOT_FOUND)
        assert _Facade.deleted == []
