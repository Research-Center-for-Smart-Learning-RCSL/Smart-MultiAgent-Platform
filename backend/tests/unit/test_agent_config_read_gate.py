"""Agent configuration is readable by its editors only (SRS §5.2 row 28).

Dossier `docs/tasks/2026-10-13-agent-config-visibility-and-project-reads/spec.md`,
AC-1, AC-2 and AC-3.

Each route is driven through FastAPI with the real gate: only the role resolver is
faked, so `decide` and the §5.2 matrix run for real. Whatever the route does after
its gate is replaced by a 418 sentinel, which makes "the gate let the caller
through" observable without building every response model, and distinguishable
from a 403 (refused) and from a 500 (the gate itself broke).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import shared_kernel.auth.dependencies as auth_deps
from app.api.v1 import agent_workspace as workspace_api
from app.api.v1 import agents as agents_api
from app.api.v1 import orchestration as orchestration_api
from app.api.v1 import skills as skills_api
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, get_role_resolver
from shared_kernel.auth.permissions import Principal, Role
from shared_kernel.db.session import db_session

_PROJECT = uuid.UUID("10000000-0000-0000-0000-000000000001")
_AGENT = uuid.UUID("20000000-0000-0000-0000-000000000002")
_SKILL = uuid.UUID("30000000-0000-0000-0000-000000000003")
_REACHED = 418


class _Resolver:
    def __init__(self, roles: frozenset[Role]) -> None:
        self._roles = roles

    async def roles_for(self, principal: Principal, scope: object) -> frozenset[Role]:
        return self._roles

    async def is_original_creator(self, **_: object) -> bool:
        return False

    async def is_chatroom_participant(self, **_: object) -> bool:
        return False


async def _reached(*_: Any, **__: Any) -> Any:
    raise HTTPException(status_code=_REACHED, detail="past the gate")


class _AgentService:
    def __init__(self, _db: object) -> None:
        pass

    async def get(self, _agent_id: uuid.UUID) -> Any:
        return SimpleNamespace(id=_AGENT, project_id=_PROJECT)

    list_for_project = staticmethod(_reached)
    list_tools = staticmethod(_reached)

    async def names_for_project(self, *_: Any, **__: Any) -> list[tuple[uuid.UUID, str]]:
        return [(_AGENT, "Teacher")]


# (role-set label) -> (roles, is_admin)
_CALLERS: dict[str, tuple[frozenset[Role], bool]] = {
    "project-member": (frozenset({Role.PROJECT_MEMBER}), False),
    "bare-org-member": (frozenset({Role.ORG_MEMBER}), False),
    "outsider": (frozenset(), False),
    "project-owner": (frozenset({Role.PROJECT_OWNER}), False),
    "org-owner": (frozenset({Role.ORG_OWNER, Role.PROJECT_OWNER}), False),
    "admin": (frozenset(), True),
}
_NON_EDITORS = ["project-member", "bare-org-member", "outsider"]
_EDITORS = ["project-owner", "org-owner", "admin"]

_CONFIG_ROUTES = [
    f"/api/projects/{_PROJECT}/agents",
    f"/api/agents/{_AGENT}",
    f"/api/agents/{_AGENT}/tools",
    f"/api/agents/{_AGENT}/workspace-files",
    f"/api/agents/{_AGENT}/skills",
    f"/api/agents/{_AGENT}/skills/{_SKILL}",
    f"/api/agents/{_AGENT}/skills/{_SKILL}/export",
    f"/api/agents/{_AGENT}/skills/{_SKILL}/files",
    f"/api/agents/{_AGENT}/skill-bindings",
    f"/api/projects/{_PROJECT}/skills",
    f"/api/projects/{_PROJECT}/skills/{_SKILL}",
    f"/api/projects/{_PROJECT}/skills/{_SKILL}/export",
    f"/api/projects/{_PROJECT}/skills/{_SKILL}/files",
    f"/api/orchestration/agents/{_AGENT}/dlq",
]
_NAMES_ROUTE = f"/api/projects/{_PROJECT}/agents/names"


@pytest.fixture
def client_for(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agents_api, "AgentService", _AgentService)
    monkeypatch.setattr(agents_api, "_to_agent_out", lambda _a: _reached_sync())
    monkeypatch.setattr(workspace_api, "AgentService", _AgentService)
    monkeypatch.setattr(workspace_api, "WorkspaceFileService", lambda *_a: _reached_sync())
    monkeypatch.setattr(skills_api, "_agent_project_id", AsyncMock(return_value=_PROJECT))
    for name in ("_list", "_get", "_export", "_list_files"):
        monkeypatch.setattr(skills_api, name, _reached)
    agents_facade = MagicMock()
    agents_facade.list_agent_tools = _reached
    monkeypatch.setattr("contexts.agents.interfaces.facade.AgentsFacade", lambda _db: agents_facade)
    dlq_facade = MagicMock()
    dlq_facade.get_agent = AsyncMock(return_value=SimpleNamespace(project_id=_PROJECT))
    monkeypatch.setattr(orchestration_api, "AgentsFacade", lambda _db: dlq_facade)
    monkeypatch.setattr(orchestration_api, "read_dlq", _reached)

    def build(caller: str) -> TestClient:
        roles, is_admin = _CALLERS[caller]
        resolver = _Resolver(roles)

        async def _inline_resolver(_db: object) -> _Resolver:
            return resolver

        # Routes that derive the project from the agent call the resolver factory
        # directly rather than through Depends, so it is replaced at module level too.
        monkeypatch.setattr(auth_deps, "get_role_resolver", _inline_resolver)
        app = FastAPI()
        for router in (
            agents_api.project_router,
            agents_api.agent_router,
            workspace_api.router,
            skills_api.agent_router,
            skills_api.project_router,
            skills_api.binding_router,
            orchestration_api.router,
        ):
            app.include_router(router)
        principal = Principal(user_id=uuid.uuid4(), is_admin=is_admin, email_verified=True)
        app.dependency_overrides[current_context] = lambda: RequestContext(principal=principal)
        app.dependency_overrides[get_role_resolver] = lambda: resolver
        db = MagicMock()
        app.dependency_overrides[db_session] = lambda: db
        return TestClient(app, raise_server_exceptions=True)

    return build


def _reached_sync() -> Any:
    raise HTTPException(status_code=_REACHED, detail="past the gate")


@pytest.mark.parametrize("path", _CONFIG_ROUTES)
@pytest.mark.parametrize("caller", _NON_EDITORS)
def test_a_non_editor_is_refused_agent_configuration(client_for, caller: str, path: str) -> None:
    response = client_for(caller).get(path)
    assert response.status_code == 403, response.text
    # AC-3: nothing of the configuration rides on the refusal.
    assert "system_prompt" not in response.text


@pytest.mark.parametrize("path", _CONFIG_ROUTES)
@pytest.mark.parametrize("caller", _EDITORS)
def test_an_editor_passes_the_gate(client_for, caller: str, path: str) -> None:
    response = client_for(caller).get(path)
    assert response.status_code == _REACHED, response.text


@pytest.mark.parametrize("caller", ["project-member", "bare-org-member", *_EDITORS])
def test_names_stay_readable_to_every_project_role(client_for, caller: str) -> None:
    response = client_for(caller).get(_NAMES_ROUTE)
    assert response.status_code == 200, response.text
    assert response.json() == [{"id": str(_AGENT), "name": "Teacher"}]
    assert "system_prompt" not in response.text


def test_names_refuse_an_outsider(client_for) -> None:
    assert client_for("outsider").get(_NAMES_ROUTE).status_code == 403
