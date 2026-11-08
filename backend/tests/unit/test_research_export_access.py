"""`still_entitled`: the export authority re-checked by the worker and the GET (F-13)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from contexts.conversation.application import research_export_access as access
from shared_kernel.auth.permissions import Role

_WS, _PROJECT, _USER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    w: dict[str, Any] = {"workspace": SimpleNamespace(project_id=_PROJECT, deleted_at=None)}
    w.update(project=object(), admin=False, roles=frozenset({Role.PROJECT_OWNER}))

    class _Workspaces:
        def __init__(self, db: object) -> None:
            pass

        async def get(self, workspace_id: uuid.UUID) -> Any:
            return w["workspace"] if workspace_id == _WS else None

    class _Tenancy:
        def __init__(self, db: object) -> None:
            pass

        async def get_project(self, project_id: uuid.UUID) -> Any:
            return w["project"]

    class _Identity:
        def __init__(self, db: object) -> None:
            pass

        async def is_admin(self, user_id: uuid.UUID) -> bool:
            return bool(w["admin"])

    class _Resolver:
        def __init__(self, db: object) -> None:
            pass

        async def roles_for(self, principal: Any, scope: Any) -> frozenset[Role]:
            assert scope.project_id == _PROJECT
            return w["roles"]  # type: ignore[no-any-return]

    monkeypatch.setattr(access, "WorkspaceRepository", _Workspaces)
    monkeypatch.setattr("contexts.tenancy.interfaces.facade.TenancyFacade", _Tenancy)
    monkeypatch.setattr("contexts.identity.interfaces.facade.IdentityFacade", _Identity)
    monkeypatch.setattr("contexts.tenancy.interfaces.role_resolver.TenancyRoleResolver", _Resolver)
    return w


async def _entitled() -> bool:
    return await access.still_entitled(object(), workspace_id=_WS, user_id=_USER)  # type: ignore[arg-type]


async def test_a_computed_project_owner_is_entitled(world: dict[str, Any]) -> None:
    assert await _entitled() is True


async def test_a_demoted_member_is_not(world: dict[str, Any]) -> None:
    world["roles"] = frozenset({Role.PROJECT_MEMBER})
    assert await _entitled() is False


async def test_an_admin_is_entitled_without_a_project_role(world: dict[str, Any]) -> None:
    world.update(admin=True, roles=frozenset())
    assert await _entitled() is True


@pytest.mark.parametrize("gone", ["workspace_missing", "workspace_deleted", "project_deleted"])
async def test_nobody_is_entitled_to_a_deleted_workspace_or_project(world: dict[str, Any], gone: str) -> None:
    world["admin"] = True
    if gone == "workspace_missing":
        world["workspace"] = None
    elif gone == "workspace_deleted":
        world["workspace"] = SimpleNamespace(project_id=_PROJECT, deleted_at=datetime.now(UTC))
    else:
        world["project"] = None
    assert await _entitled() is False
