"""`/api/projects/{pid}/keys/*` — carry / withdraw (§22.4, D.5).

Mounts on a separate router so `/api/keys/*` (individual surface) and this
(project surface) keep distinct scopes for AuthZ resolution. Scopes are
built from the path `project_id` param via `scope_from_path`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.keys import KeyOut
from contexts.keys.application.carry_service import CarryService, UsageSummary
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    current_context,
    current_principal,
    require,
    require_membership,
    scope_from_path,
)
from shared_kernel.auth.permissions import Capability, Principal
from shared_kernel.db.session import db_session
from typing import Literal


class UsageOut(BaseModel):
    window: str
    input_tokens: int
    output_tokens: int
    requests: int
    errors: int

    @classmethod
    def from_domain(cls, u: UsageSummary) -> "UsageOut":
        return cls(
            window=u.window, input_tokens=u.input_tokens,
            output_tokens=u.output_tokens, requests=u.requests, errors=u.errors,
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


@router.delete(
    "/{project_id}/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        # KEY_DELETE_OWN (own carry) OR KEY_DELETE_OTHER (PO withdraw any).
        # The matrix accepts either; the service enforces ownership rules.
        Depends(
            require(Capability.KEY_DELETE_OWN, scope_from_path(project_param="project_id"))
        ),
    ],
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
    return UsageOut.from_domain(
        await svc.usage(key_id=key_id, project_id=project_id, window=window)
    )
