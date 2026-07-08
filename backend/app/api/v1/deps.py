"""Shared FastAPI dependencies for the v1 API layer."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Query
from sqlalchemy.ext.asyncio import AsyncSession

from shared_kernel.auth.permissions import Principal


@dataclass(frozen=True, slots=True)
class PaginationParams:
    """Reusable limit/offset pagination extracted from query-string params."""

    limit: int = Query(100, ge=1, le=500, description="Max items to return")  # noqa: RUF009
    offset: int = Query(0, ge=0, description="Number of items to skip")  # noqa: RUF009


async def assert_project_owner(
    *,
    db: AsyncSession,
    principal: Principal,
    project_id: uuid.UUID,
    reason: str = "only a project owner may perform this action",
) -> None:
    """Raise 403 unless the principal is a strict Project Owner (admin bypasses).

    The single owner-gate for route handlers so the admin-bypass + owner-check
    decision lives in one place instead of being copy-pasted (and reaching into
    the private ``_raise_forbidden``) across routers.
    """
    from contexts.tenancy.interfaces.facade import TenancyFacade
    from shared_kernel.auth.dependencies import _raise_forbidden

    if principal.is_admin:
        return
    if not await TenancyFacade(db).is_project_owner(principal.user_id, project_id):
        _raise_forbidden(reason)
