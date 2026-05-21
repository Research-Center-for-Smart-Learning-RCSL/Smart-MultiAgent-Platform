"""`/api/projects/{pid}/keys/*` — carry / withdraw (§22.4, D.5).

Mounts on a separate router so `/api/keys/*` (individual surface) and this
(project surface) keep distinct scopes for AuthZ resolution. Scopes are
built from the path `project_id` param via `scope_from_path`.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.keys import KeyOut
from contexts.keys.application.carry_service import CarryService, UsageSummary
from contexts.keys.domain.errors import KeyNotFound
from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    _raise_forbidden,
    current_context,
    current_principal,
    get_role_resolver,
    require,
    require_membership,
    scope_from_path,
)
from shared_kernel.auth.permissions import Capability, Principal, Scope, decide
from shared_kernel.db.session import db_session


class UsageOut(BaseModel):
    window: str
    input_tokens: int
    output_tokens: int
    requests: int
    errors: int

    @classmethod
    def from_domain(cls, u: UsageSummary) -> UsageOut:
        return cls(
            window=u.window,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            requests=u.requests,
            errors=u.errors,
        )


router = APIRouter(prefix="/api/projects", tags=["keys"])


class CarryIn(BaseModel):
    key_id: uuid.UUID


@router.get(
    "/{project_id}/keys",
    response_model=list[KeyOut],
    dependencies=[Depends(require_membership(project_param="project_id"))],
)
async def list_carried_keys(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(db_session),
) -> list[KeyOut]:
    svc = CarryService(db)
    keys = await svc.list_in_project(project_id)
    return [KeyOut.from_domain(k) for k in keys]


@router.post(
    "/{project_id}/keys",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[
        Depends(require(Capability.KEY_UPLOAD, scope_from_path(project_param="project_id"))),
    ],
)
async def carry_key(
    project_id: uuid.UUID,
    payload: CarryIn,
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    svc = CarryService(db)
    await svc.carry(
        key_id=payload.key_id,
        project_id=project_id,
        caller_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


async def require_withdraw(
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    resolver: TenancyRoleResolver = Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
) -> None:
    """AuthZ gate for `DELETE /{project_id}/keys/{key_id}` (SEC-5).

    Withdraw is two capabilities behind one route: the key's owner withdraws
    their own carry (`key.delete_own`, an `OWN_ONLY` row), and a Project/Org
    Owner withdraws anyone's (`key.delete_other`). `OWN_ONLY` is undecidable
    until the key's owner is known — and `scope_from_path` cannot reach the DB
    — so the owner is resolved here and fed into `Scope.resource_owner_user_id`
    before `decide` runs. An unknown key fails closed as 404.
    """
    owner_id = await CarryService(db).key_owner(key_id)
    if owner_id is None:
        raise KeyNotFound(str(key_id))
    scope = Scope(project_id=project_id, resource_owner_user_id=owner_id)
    decision = await decide(principal, Capability.KEY_DELETE_OWN, scope, resolver)
    if not decision.allowed:
        decision = await decide(
            principal, Capability.KEY_DELETE_OTHER_IN_PROJECT, scope, resolver
        )
    if not decision.allowed:
        _raise_forbidden(decision.reason)


@router.delete(
    "/{project_id}/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(require_withdraw)],
)
async def withdraw_key(
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    svc = CarryService(db)
    await svc.withdraw(
        key_id=key_id,
        project_id=project_id,
        caller_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.get(
    "/{project_id}/keys/{key_id}/usage",
    response_model=UsageOut,
    dependencies=[
        # R7.05: any Project member with usage-view capability may read the
        # aggregate; no secret is exposed. §5.2 capability 5.
        Depends(
            require(
                Capability.KEY_VIEW_USAGE_PROJECT,
                scope_from_path(project_param="project_id"),
            )
        ),
    ],
)
async def read_usage(
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    window: Literal["1h", "24h", "7d", "30d"] = Query("1h"),
    db: AsyncSession = Depends(db_session),
) -> UsageOut:
    svc = CarryService(db)
    return UsageOut.from_domain(await svc.usage(key_id=key_id, project_id=project_id, window=window))
