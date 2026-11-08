"""Who may still receive a workspace's research export ([R33.10], [R33.07]).

Checked when the export is requested, again when the worker builds it, and
again on every download, so a requester demoted in between gets nothing
(audit 2026-10-05 F-13). Admin status is read from the database because the
worker has no request principal.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.infrastructure.repositories import WorkspaceRepository
from shared_kernel.auth.permissions import Principal, Role, Scope


async def still_entitled(db: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """A live workspace in a live project, and the user is an admin or its computed owner."""
    from contexts.identity.interfaces.facade import IdentityFacade
    from contexts.tenancy.interfaces.facade import TenancyFacade
    from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver

    workspace = await WorkspaceRepository(db).get(workspace_id)
    if workspace is None or workspace.deleted_at is not None:
        return False
    if await TenancyFacade(db).get_project(workspace.project_id) is None:
        return False
    if await IdentityFacade(db).is_admin(user_id):
        return True
    principal = Principal(user_id=user_id, is_admin=False, email_verified=True)
    roles = await TenancyRoleResolver(db).roles_for(principal, Scope(project_id=workspace.project_id))
    return Role.PROJECT_OWNER in roles


__all__ = ["still_entitled"]
