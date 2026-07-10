"""assert_project_membership — the shared membership-gate (deps.py).

Was hand-copied identically across graphrag.py, knowmap.py, and
agent_groups.py; collapsed into one helper (code review, 2026-07-10).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1 import deps as deps_mod


@pytest.mark.asyncio
async def test_admin_bypasses_role_lookup() -> None:
    principal = SimpleNamespace(is_admin=True, user_id=uuid.uuid4())
    resolver_calls = AsyncMock()
    with patch("shared_kernel.auth.dependencies.get_role_resolver", AsyncMock(return_value=resolver_calls)):
        await deps_mod.assert_project_membership(db=AsyncMock(), principal=principal, project_id=uuid.uuid4())
    resolver_calls.roles_for.assert_not_called()


@pytest.mark.asyncio
async def test_member_passes() -> None:
    principal = SimpleNamespace(is_admin=False, user_id=uuid.uuid4())
    resolver = AsyncMock()
    resolver.roles_for = AsyncMock(return_value=("member",))
    with patch("shared_kernel.auth.dependencies.get_role_resolver", AsyncMock(return_value=resolver)):
        await deps_mod.assert_project_membership(db=AsyncMock(), principal=principal, project_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_non_member_raises_403_with_default_reason() -> None:
    principal = SimpleNamespace(is_admin=False, user_id=uuid.uuid4())
    resolver = AsyncMock()
    resolver.roles_for = AsyncMock(return_value=())
    with (
        patch("shared_kernel.auth.dependencies.get_role_resolver", AsyncMock(return_value=resolver)),
        pytest.raises(HTTPException) as exc,
    ):
        await deps_mod.assert_project_membership(db=AsyncMock(), principal=principal, project_id=uuid.uuid4())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_non_member_raises_with_custom_reason() -> None:
    principal = SimpleNamespace(is_admin=False, user_id=uuid.uuid4())
    resolver = AsyncMock()
    resolver.roles_for = AsyncMock(return_value=())
    with (
        patch("shared_kernel.auth.dependencies.get_role_resolver", AsyncMock(return_value=resolver)),
        pytest.raises(HTTPException) as exc,
    ):
        await deps_mod.assert_project_membership(
            db=AsyncMock(),
            principal=principal,
            project_id=uuid.uuid4(),
            reason="caller is not a member of the group's project",
        )
    assert exc.value.status_code == 403
    assert "group's project" in str(exc.value.detail)
