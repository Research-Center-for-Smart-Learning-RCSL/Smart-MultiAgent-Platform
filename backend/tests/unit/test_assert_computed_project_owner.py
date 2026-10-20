"""Computed owner gates include inherited Org Owners and reject deleted projects."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import shared_kernel.auth.dependencies as auth_deps
from app.api.v1.deps import assert_computed_project_owner
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel.auth.permissions import Principal, Role


class _Resolver:
    def __init__(self, roles: frozenset[Role]) -> None:
        self.roles = roles

    async def roles_for(self, _principal: Principal, _scope: object) -> frozenset[Role]:
        return self.roles


@pytest.mark.asyncio
async def test_inherited_project_owner_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TenancyFacade, "get_project", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        auth_deps, "get_role_resolver", AsyncMock(return_value=_Resolver(frozenset({Role.PROJECT_OWNER})))
    )

    await assert_computed_project_owner(
        db=object(),
        principal=Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True),
        project_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_project_member_is_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TenancyFacade, "get_project", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        auth_deps, "get_role_resolver", AsyncMock(return_value=_Resolver(frozenset({Role.PROJECT_MEMBER})))
    )

    with pytest.raises(HTTPException) as exc:
        await assert_computed_project_owner(
            db=object(),
            principal=Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True),
            project_id=uuid.uuid4(),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_bypasses_role_resolution_but_not_project_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_lookup = AsyncMock(return_value=None)
    role_lookup = AsyncMock()
    monkeypatch.setattr(TenancyFacade, "get_project", project_lookup)
    monkeypatch.setattr(auth_deps, "get_role_resolver", role_lookup)

    with pytest.raises(HTTPException) as exc:
        await assert_computed_project_owner(
            db=object(),
            principal=Principal(user_id=uuid.uuid4(), is_admin=True, email_verified=True),
            project_id=uuid.uuid4(),
        )

    assert exc.value.status_code == 404
    role_lookup.assert_not_awaited()
