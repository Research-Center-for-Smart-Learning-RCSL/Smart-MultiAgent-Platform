"""The workflow read surface is backstage, not project-wide ([R14.10], AC-6).

Dossier `docs/tasks/2026-08-20-orchestration-room-scoped-reads/spec.md` §4.2:
[R14.10] has always said Admin + Project Owners, and `workflows.py` granted the
whole trace to any project member. These pin the restored rule and the two
places a reader could learn it from — the gate and the module docstring.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.v1 import workflows
from app.api.v1.deps import PaginationParams
from shared_kernel.auth.permissions import Principal, Role, Scope

_PROJECT = uuid.uuid4()
_SCOPE = Scope(project_id=_PROJECT)


def _principal(*, is_admin: bool = False) -> Principal:
    return Principal(user_id=uuid.uuid4(), is_admin=is_admin, email_verified=True)


class _Resolver:
    def __init__(self, roles: frozenset[Role]) -> None:
        self.roles = roles

    async def roles_for(self, principal: Principal, scope: Scope) -> frozenset[Role]:
        return self.roles


@pytest.mark.parametrize(
    ("roles", "allowed"),
    [
        (frozenset({Role.PROJECT_OWNER}), True),
        (frozenset({Role.ORG_OWNER}), True),
        (frozenset({Role.PROJECT_MEMBER}), False),
        (frozenset({Role.ORG_MEMBER}), False),
        (frozenset({Role.PROJECT_MEMBER, Role.ORG_MEMBER}), False),
        (frozenset(), False),
    ],
)
async def test_require_moderator_matches_r14_10(roles: frozenset[Role], allowed: bool) -> None:
    call = workflows._require_moderator(_principal(), _SCOPE, _Resolver(roles))
    if allowed:
        await call
        return
    with pytest.raises(HTTPException) as exc:
        await call
    assert exc.value.status_code == 403


async def test_admin_passes_without_a_role_lookup() -> None:
    class _Exploding:
        async def roles_for(self, principal: Principal, scope: Scope) -> frozenset[Role]:
            raise AssertionError("admin must short-circuit")

    await workflows._require_moderator(_principal(is_admin=True), _SCOPE, _Exploding())


# ---------------------------------------------------------------------------
# Every read route runs the gate, and runs it before reading anything
# ---------------------------------------------------------------------------


def _service(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    svc = MagicMock()
    svc.list_for_workspace = AsyncMock(return_value=[])
    svc.list_runs = AsyncMock(return_value=[])
    svc.get_run = AsyncMock(return_value=MagicMock())
    svc.list_steps = AsyncMock(return_value=[])
    svc.validate = MagicMock()
    monkeypatch.setattr(workflows, "WorkflowService", lambda _db: svc)
    return svc


def _read_routes() -> dict[str, Any]:
    member = _principal()
    resolver = _Resolver(frozenset({Role.PROJECT_MEMBER}))
    common = {"scope": _SCOPE, "principal": member, "resolver": resolver, "db": MagicMock()}
    return {
        "list_workflows": lambda: workflows.list_workflows(
            wid=uuid.uuid4(), pagination=PaginationParams(limit=50, offset=0), **common
        ),
        "validate_workflow": lambda: workflows.validate_workflow(
            payload=workflows.ValidateIn(definition={"schema_version": "1.0"}),
            wid=uuid.uuid4(),
            **common,
        ),
        "list_runs": lambda: workflows.list_runs(
            workflow_id=uuid.uuid4(), limit=50, offset=0, include_archive=False, **common
        ),
        "get_run": lambda: workflows.get_run(run_id=uuid.uuid4(), **common),
        "list_steps": lambda: workflows.list_steps(run_id=uuid.uuid4(), **common),
    }


@pytest.mark.parametrize("route_name", list(_read_routes()))
async def test_read_route_refuses_a_plain_project_member(
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
) -> None:
    svc = _service(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await _read_routes()[route_name]()

    assert exc.value.status_code == 403
    # Nothing was read before the refusal.
    for reader in (svc.list_for_workspace, svc.list_runs, svc.get_run, svc.list_steps):
        reader.assert_not_awaited()
    svc.validate.assert_not_called()


def test_module_docstring_states_the_backstage_rule() -> None:
    # AC-6's second half: the docstring was the authoritative-looking statement
    # of the wrong rule, and whoever reads it next copies it.
    doc = workflows.__doc__ or ""
    assert "project membership for read" not in doc
    assert "R14.10" in doc
