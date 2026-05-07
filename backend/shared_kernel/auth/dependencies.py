"""FastAPI dependency factories that plug the auth layer into routers.

Usage inside a handler:

    @router.get("/api/orgs/{id}")
    async def read_org(
        id: UUID,
        principal: Principal = Depends(current_principal),
        _=Depends(require(Capability.ORG_MEMBER_MANAGE, scope_from_path("org_id"))),
        db: AsyncSession = Depends(db_session),
    ): ...

`require(...)` is the single AuthZ tap — every server-side check flows
through `permissions.decide` (R5.05).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.permissions import (
    Capability,
    Decision,
    Principal,
    Scope,
    decide,
)
from shared_kernel.db.session import db_session
from shared_kernel.errors.problem import Problem, problem_type

ScopeBuilder = Callable[[Request], Awaitable[Scope]]


async def current_context(request: Request) -> RequestContext:
    ctx: RequestContext | None = getattr(request.state, "auth_ctx", None)
    if ctx is None:
        ctx = RequestContext()
        request.state.auth_ctx = ctx
    return ctx


async def current_principal(ctx: RequestContext = Depends(current_context)) -> Principal:
    if ctx.principal is None:
        _raise_unauth()
    return ctx.principal  # type: ignore[return-value]


async def optional_principal(
    ctx: RequestContext = Depends(current_context),
) -> Principal | None:
    return ctx.principal


async def get_role_resolver(
    db: AsyncSession = Depends(db_session),
) -> TenancyRoleResolver:
    return TenancyRoleResolver(db)


def require(
    capability: Capability,
    scope_from: ScopeBuilder | Scope | None = None,
) -> Callable[..., Awaitable[Decision]]:
    """Produce a dependency that raises 403 unless the caller is allowed."""

    async def dep(
        request: Request,
        principal: Principal = Depends(current_principal),
        resolver: TenancyRoleResolver = Depends(get_role_resolver),
    ) -> Decision:
        if scope_from is None:
            scope = Scope()
        elif isinstance(scope_from, Scope):
            scope = scope_from
        else:
            scope = await scope_from(request)
        decision = await decide(principal, capability, scope, resolver)
        if not decision.allowed:
            _raise_forbidden(decision.reason)
        return decision

    return dep


def scope_from_path(
    *,
    org_param: str | None = None,
    project_param: str | None = None,
    chatroom_param: str | None = None,
    resource_owner_param: str | None = None,
) -> ScopeBuilder:
    async def build(request: Request) -> Scope:
        params = request.path_params
        return Scope(
            org_id=_uuid(params.get(org_param)) if org_param else None,
            project_id=_uuid(params.get(project_param)) if project_param else None,
            chatroom_id=_uuid(params.get(chatroom_param)) if chatroom_param else None,
            resource_owner_user_id=(
                _uuid(params.get(resource_owner_param)) if resource_owner_param else None
            ),
        )

    return build


def _uuid(raw: object) -> uuid.UUID | None:
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


def require_membership(
    *,
    org_param: str | None = None,
    project_param: str | None = None,
) -> Callable[..., Awaitable[None]]:
    """Assert the caller is a member (any role) of the target org or project.

    Use for *read-only* endpoints where `require(Capability, ...)` would be
    over-restrictive (the matrix is privilege-centric; bare membership is
    below the lowest capability row). Admin always passes.
    """
    builder = scope_from_path(org_param=org_param, project_param=project_param)

    async def dep(
        request: Request,
        principal: Principal = Depends(current_principal),
        resolver: TenancyRoleResolver = Depends(get_role_resolver),
    ) -> None:
        if principal.is_admin:
            return
        scope = await builder(request)
        if scope.org_id is None and scope.project_id is None:
            _raise_forbidden("membership check without scope")
        roles = await resolver.roles_for(principal, scope)
        if not roles:
            _raise_forbidden("caller is not a member of the scope")

    return dep


def _raise_unauth() -> None:
    problem = Problem(
        type=problem_type("auth/required"),
        title="Authentication required",
        status=401,
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=problem.dump(),
        headers={"Content-Type": "application/problem+json"},
    )


def _raise_forbidden(reason: str) -> None:
    problem = Problem(
        type=problem_type("forbidden"),
        title="Forbidden",
        status=403,
        detail=reason,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=problem.dump(),
        headers={"Content-Type": "application/problem+json"},
    )


__all__ = [
    "current_context",
    "current_principal",
    "get_role_resolver",
    "optional_principal",
    "require",
    "require_membership",
    "scope_from_path",
]
