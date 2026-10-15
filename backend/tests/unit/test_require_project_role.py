"""The project-role gate for project-scoped reads ([R5.07]).

Dossier `docs/tasks/2026-10-13-agent-config-visibility-and-project-reads/spec.md`,
stage 2: AC-6 (refusal of a bare Org Member on every stage-2 read) and AC-7 (the
room-navigation reads keep admitting one).

Three layers, because the gate is spread over three shapes:

* the dependency `require_project_role` and the helpers `has_project_role` /
  `assert_project_role`, driven for every caller type;
* the route table: every project-scoped route that still takes the membership
  gate must be on the room-navigation path, and every stage-2 dependency route
  takes the project-role gate. Read from the registry the app mounts, so a new
  route is covered without being listed;
* the inline routes, which derive the project from a resource and so cannot be
  read off the route table: each must call one of the helpers.

The role resolution itself, against real rows, is `test_project_role_gate_db.py`.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import shared_kernel.auth.dependencies as auth_deps
from app.api.v1 import (
    activities,
    agent_groups,
    canvas_templates,
    dashboard,
    get_router_registry,
    graphrag,
    knowmap,
    rag,
)
from app.api.v1 import deps as deps_mod
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, get_role_resolver, require_project_role
from shared_kernel.auth.permissions import Principal, Role, holds_project_role

_PROJECT = uuid.UUID("50000000-0000-0000-0000-000000000005")


class _Resolver:
    def __init__(self, roles: frozenset[Role]) -> None:
        self._roles = roles

    async def roles_for(self, principal: Principal, scope: object) -> frozenset[Role]:
        return self._roles


# label -> (roles at project scope, is_admin, admitted)
_CALLERS: dict[str, tuple[frozenset[Role], bool, bool]] = {
    "admin": (frozenset(), True, True),
    "org-owner": (frozenset({Role.ORG_OWNER, Role.PROJECT_OWNER}), False, True),
    "project-owner": (frozenset({Role.PROJECT_OWNER}), False, True),
    "project-member": (frozenset({Role.PROJECT_MEMBER}), False, True),
    # A project member who also belongs to the parent org holds both.
    "project-member-and-org-member": (frozenset({Role.PROJECT_MEMBER, Role.ORG_MEMBER}), False, True),
    "bare-org-member": (frozenset({Role.ORG_MEMBER}), False, False),
    "outsider": (frozenset(), False, False),
}


def _principal(is_admin: bool) -> Principal:
    return Principal(user_id=uuid.uuid4(), is_admin=is_admin, email_verified=True)


# --------------------------------------------------------------------------- #
# The predicate, the dependency and the helpers
# --------------------------------------------------------------------------- #


# Admin is left out: it is the caller's bypass, not part of the role predicate.
@pytest.mark.parametrize("caller", [c for c, (_, is_admin, _) in _CALLERS.items() if not is_admin])
def test_the_predicate(caller: str) -> None:
    roles, _, admitted = _CALLERS[caller]
    assert holds_project_role(roles) is admitted


@pytest.mark.parametrize("caller", list(_CALLERS))
def test_the_dependency(caller: str) -> None:
    roles, is_admin, admitted = _CALLERS[caller]
    router = APIRouter()

    @router.get("/p/{project_id}", dependencies=[Depends(require_project_role(project_param="project_id"))])
    async def _read(project_id: uuid.UUID) -> dict[str, str]:
        return {"ok": "yes"}

    app = FastAPI()
    app.include_router(router)
    principal = _principal(is_admin)
    app.dependency_overrides[current_context] = lambda: RequestContext(principal=principal)
    resolver = _Resolver(roles)
    app.dependency_overrides[get_role_resolver] = lambda: resolver

    response = TestClient(app).get(f"/p/{_PROJECT}")

    assert response.status_code == (200 if admitted else 403), response.text


@pytest.mark.parametrize("caller", list(_CALLERS))
async def test_the_helpers(monkeypatch: pytest.MonkeyPatch, caller: str) -> None:
    roles, is_admin, admitted = _CALLERS[caller]

    async def _resolver(_db: object) -> _Resolver:
        return _Resolver(roles)

    monkeypatch.setattr(auth_deps, "get_role_resolver", _resolver)
    principal = _principal(is_admin)

    assert (
        await deps_mod.has_project_role(db=MagicMock(), principal=principal, project_id=_PROJECT) is admitted
    )
    if admitted:
        await deps_mod.assert_project_role(db=MagicMock(), principal=principal, project_id=_PROJECT)
    else:
        with pytest.raises(HTTPException) as exc:
            await deps_mod.assert_project_role(db=MagicMock(), principal=principal, project_id=_PROJECT)
        assert exc.value.status_code == 403


# --------------------------------------------------------------------------- #
# The route table
# --------------------------------------------------------------------------- #

# [R5.07] / Q-7: the project-scoped reads on the way to a room that admits org
# members. The other two room-path reads are workspace-scoped and resolve the
# project inside the handler (`workspaces.py`, `chatrooms.py`).
_ROOM_PATH = {
    ("GET", "/api/projects/{project_id}"),
    ("GET", "/api/projects/{project_id}/workspaces"),
    ("GET", "/api/projects/{project_id}/agents/names"),
}

# Stage 2's dependency-gated routes (§6).
_STAGE_2_DEPENDENCY_ROUTES = {
    ("GET", "/api/projects/{project_id}/members"),
    ("GET", "/api/projects/{project_id}/keys"),
    ("GET", "/api/projects/{project_id}/key-groups"),
    ("GET", "/api/projects/{project_id}/search-keys"),
    ("GET", "/api/projects/{project_id}/rag-configs"),
    ("GET", "/api/projects/{project_id}/graphrag-configs"),
    ("GET", "/api/projects/{project_id}/graphrag-configs/owner-options"),
    ("GET", "/api/projects/{project_id}/knowmap-configs"),
    ("GET", "/api/projects/{project_id}/mcp/egress-allowlist"),
    ("GET", "/api/projects/{project_id}/member-groups"),
    ("GET", "/api/projects/{project_id}/prompt-assistant"),
    ("GET", "/api/projects/{project_id}/prompt-templates"),
    ("POST", "/api/projects/{project_id}/prompt-assistant/sessions"),
}


def _gates(dependant: Any, out: set[str]) -> set[str]:
    for dep in dependant.dependencies:
        name = getattr(dep.call, "__qualname__", "")
        if name.startswith(("require_membership.", "require_project_role.")):
            out.add(name.split(".")[0])
        _gates(dep, out)
    return out


def _project_scoped_gates() -> dict[tuple[str, str], set[str]]:
    table: dict[tuple[str, str], set[str]] = {}
    for entry in get_router_registry():
        for route in entry.router.routes:
            if not isinstance(route, APIRoute) or "{project_id}" not in route.path:
                continue
            gates = _gates(route.dependant, set())
            for method in route.methods:
                table[(method, route.path)] = gates
    return table


def test_the_route_table_is_read() -> None:
    # A sweep that finds nothing proves nothing.
    assert ("GET", "/api/projects/{project_id}/members") in _project_scoped_gates()


def test_only_the_room_path_keeps_the_membership_gate() -> None:
    on_membership = {key for key, gates in _project_scoped_gates().items() if "require_membership" in gates}
    assert on_membership == _ROOM_PATH


def test_every_stage_2_dependency_route_takes_the_project_role_gate() -> None:
    table = _project_scoped_gates()
    missing = {
        key for key in _STAGE_2_DEPENDENCY_ROUTES if "require_project_role" not in table.get(key, set())
    }
    assert missing == set()


# --------------------------------------------------------------------------- #
# The inline routes
# --------------------------------------------------------------------------- #

_INLINE = [
    rag.read_rag_config,
    rag.list_documents,
    knowmap.read_knowmap_config,
    knowmap.read_knowmap_graph,
    knowmap.list_knowmap_documents,
    graphrag.read_agent_concept_map_coverage,
    agent_groups.list_groups,
    agent_groups.get_group,
    agent_groups.list_members,
    activities.list_activity_types,
    canvas_templates.list_templates,
    canvas_templates._can_read_template,
    dashboard._resolve_workspace_rooms,
]


def _called_names(fn: Any) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


@pytest.mark.parametrize("fn", _INLINE, ids=lambda f: f"{f.__module__.rsplit('.', 1)[-1]}.{f.__name__}")
def test_every_inline_stage_2_read_calls_the_project_role_helper(fn: Any) -> None:
    called = _called_names(fn)
    assert called & {"assert_project_role", "has_project_role"}, called
