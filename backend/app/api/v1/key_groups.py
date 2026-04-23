"""`/api/projects/{pid}/key-groups/*` + `/api/key-groups/{gid}/*` (§22.5).

Two mount points so path-param AuthZ (`project_id` at collection level,
`group_id` at item level) remains clean. Scope for item-level endpoints is
resolved by the service (group → project lookup) at the time of the call;
the `require(...)` dependency uses project-level scope builders where the
URL carries a project_id, and a membership check otherwise.
"""

from __future__ import annotations

import uuid

from fastapi import Response, APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.application.group_service import KeyGroupService, MemberPatchInput
from contexts.keys.domain.errors import KeyNotFound
from contexts.keys.domain.groups import KeyGroup, KeyGroupMember
from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    current_context,
    current_principal,
    get_role_resolver,
    require,
    require_membership,
    scope_from_path,
)
from shared_kernel.auth.permissions import Capability, Principal, Scope, decide
from shared_kernel.db.session import db_session
from shared_kernel.errors.problem import Problem, problem_type


project_router = APIRouter(prefix="/api/projects", tags=["key-groups"])
group_router = APIRouter(prefix="/api/key-groups", tags=["key-groups"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class GroupOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    created_at: str

    @classmethod
    def from_domain(cls, g: KeyGroup) -> "GroupOut":
        return cls(
            id=g.id, project_id=g.project_id, name=g.name,
            created_at=g.created_at.isoformat(),
        )


class RotationOut(BaseModel):
    rotate_on_error_codes: list[int]
    rotate_on_token_quota: bool
    retry_on_error: bool
    retry_initial_delay_ms: int
    retry_multiplier: float
    retry_max_delay_ms: int
    retry_max: int
    retry_jitter_pct: int


class LimitsOut(BaseModel):
    max_input_tokens_per_hour: int | None
    max_output_tokens_per_hour: int | None
    max_requests_per_hour: int | None


class MemberOut(BaseModel):
    key_id: uuid.UUID
    priority: int
    rotation: RotationOut
    limits: LimitsOut

    @classmethod
    def from_domain(cls, m: KeyGroupMember) -> "MemberOut":
        return cls(
            key_id=m.key_id,
            priority=m.priority,
            rotation=RotationOut(
                rotate_on_error_codes=list(m.rotation.rotate_on_error_codes),
                rotate_on_token_quota=m.rotation.rotate_on_token_quota,
                retry_on_error=m.rotation.retry_on_error,
                retry_initial_delay_ms=m.rotation.retry_initial_delay_ms,
                retry_multiplier=float(m.rotation.retry_multiplier),
                retry_max_delay_ms=m.rotation.retry_max_delay_ms,
                retry_max=m.rotation.retry_max,
                retry_jitter_pct=m.rotation.retry_jitter_pct,
            ),
            limits=LimitsOut(
                max_input_tokens_per_hour=m.limits.max_input_tokens_per_hour,
                max_output_tokens_per_hour=m.limits.max_output_tokens_per_hour,
                max_requests_per_hour=m.limits.max_requests_per_hour,
            ),
        )


class GroupDetailOut(BaseModel):
    group: GroupOut
    members: list[MemberOut]


class AddMemberIn(BaseModel):
    key_id: uuid.UUID


class MemberPatchIn(BaseModel):
    priority: int | None = Field(default=None, ge=1)
    rotate_on_error_codes: list[int] | None = None
    rotate_on_token_quota: bool | None = None
    retry_on_error: bool | None = None
    retry_initial_delay_ms: int | None = Field(default=None, ge=0)
    retry_multiplier: float | None = Field(default=None, gt=0)
    retry_max_delay_ms: int | None = Field(default=None, ge=0)
    retry_max: int | None = Field(default=None, ge=0)
    retry_jitter_pct: int | None = Field(default=None, ge=0, le=100)
    max_input_tokens_per_hour: int | None = Field(default=None, ge=0)
    max_output_tokens_per_hour: int | None = Field(default=None, ge=0)
    max_requests_per_hour: int | None = Field(default=None, ge=0)


class ReorderIn(BaseModel):
    priorities: dict[uuid.UUID, int]


class GroupPatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------------------
# Handlers — project-scoped collection
# ---------------------------------------------------------------------------


@project_router.get(
    "/{project_id}/key-groups",
    response_model=list[GroupOut],
    dependencies=[Depends(require_membership(project_param="project_id"))],
)
async def list_groups(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(db_session),
) -> list[GroupOut]:
    return [
        GroupOut.from_domain(g)
        for g in await KeyGroupService(db).list_for_project(project_id)
    ]


@project_router.post(
    "/{project_id}/key-groups",
    response_model=GroupOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require(Capability.KEY_CONFIGURE, scope_from_path(project_param="project_id"))),
    ],
)
async def create_group(
    project_id: uuid.UUID,
    payload: GroupIn,
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> GroupOut:
    g = await KeyGroupService(db).create(
        project_id=project_id,
        name=payload.name,
        actor_user_id=principal.user_id,
        request_id=ctx.request_id,
    )
    return GroupOut.from_domain(g)


# ---------------------------------------------------------------------------
# Handlers — group-scoped item
# ---------------------------------------------------------------------------


def _require_group_cap(capability: Capability):
    """Look up the group's project and gate on `capability`.

    Item-level group endpoints only know `group_id`, so we do one DB read
    to materialise the project scope. Returning 404 when the group is
    missing (before the AuthZ decision) avoids leaking existence to
    unauthorised callers.
    """

    async def dep(
        group_id: uuid.UUID,
        principal: Principal = Depends(current_principal),
        resolver: TenancyRoleResolver = Depends(get_role_resolver),
        db: AsyncSession = Depends(db_session),
    ) -> None:
        loaded = await KeyGroupService(db).get_with_members(group_id)
        if loaded is None:
            raise KeyNotFound(str(group_id))
        group, _ = loaded
        decision = await decide(
            principal, capability,
            Scope(project_id=group.project_id), resolver,
        )
        if not decision.allowed:
            p = Problem(
                type=problem_type("forbidden"), title="Forbidden", status=403,
                detail=decision.reason,
            )
            raise HTTPException(status_code=403, detail=p.dump())

    return dep


_require_group_configure = _require_group_cap(Capability.KEY_CONFIGURE)
_require_group_view = _require_group_cap(Capability.KEY_VIEW_USAGE_PROJECT)


@group_router.get("/{group_id}", response_model=GroupDetailOut)
async def read_group(
    group_id: uuid.UUID,
    _: None = Depends(_require_group_view),
    db: AsyncSession = Depends(db_session),
) -> GroupDetailOut:
    loaded = await KeyGroupService(db).get_with_members(group_id)
    if loaded is None:
        raise KeyNotFound(str(group_id))
    group, members = loaded
    return GroupDetailOut(
        group=GroupOut.from_domain(group),
        members=[MemberOut.from_domain(m) for m in members],
    )


@group_router.patch("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def rename_group(
    group_id: uuid.UUID,
    payload: GroupPatchIn,
    _: None = Depends(_require_group_configure),
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    await KeyGroupService(db).rename(
        group_id=group_id, name=payload.name,
        actor_user_id=principal.user_id, request_id=ctx.request_id,
    )


@group_router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: uuid.UUID,
    _: None = Depends(_require_group_configure),
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    await KeyGroupService(db).delete(
        group_id=group_id,
        actor_user_id=principal.user_id,
        request_id=ctx.request_id,
    )


@group_router.post(
    "/{group_id}/keys",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    group_id: uuid.UUID,
    payload: AddMemberIn,
    _: None = Depends(_require_group_configure),
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> MemberOut:
    m = await KeyGroupService(db).add_member(
        group_id=group_id,
        key_id=payload.key_id,
        actor_user_id=principal.user_id,
        request_id=ctx.request_id,
    )
    return MemberOut.from_domain(m)


@group_router.patch(
    "/{group_id}/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def patch_member(
    group_id: uuid.UUID,
    key_id: uuid.UUID,
    payload: MemberPatchIn,
    _: None = Depends(_require_group_configure),
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    await KeyGroupService(db).patch_member(
        group_id=group_id,
        key_id=key_id,
        updates=MemberPatchInput(
            priority=payload.priority,
            rotate_on_error_codes=(
                tuple(payload.rotate_on_error_codes)
                if payload.rotate_on_error_codes is not None else None
            ),
            rotate_on_token_quota=payload.rotate_on_token_quota,
            retry_on_error=payload.retry_on_error,
            retry_initial_delay_ms=payload.retry_initial_delay_ms,
            retry_multiplier=payload.retry_multiplier,
            retry_max_delay_ms=payload.retry_max_delay_ms,
            retry_max=payload.retry_max,
            retry_jitter_pct=payload.retry_jitter_pct,
            max_input_tokens_per_hour=payload.max_input_tokens_per_hour,
            max_output_tokens_per_hour=payload.max_output_tokens_per_hour,
            max_requests_per_hour=payload.max_requests_per_hour,
        ),
        actor_user_id=principal.user_id,
        request_id=ctx.request_id,
    )


@group_router.delete(
    "/{group_id}/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    group_id: uuid.UUID,
    key_id: uuid.UUID,
    _: None = Depends(_require_group_configure),
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    await KeyGroupService(db).remove_member(
        group_id=group_id,
        key_id=key_id,
        actor_user_id=principal.user_id,
        request_id=ctx.request_id,
    )


@group_router.post(
    "/{group_id}/reorder",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reorder_members(
    group_id: uuid.UUID,
    payload: ReorderIn,
    _: None = Depends(_require_group_configure),
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    await KeyGroupService(db).reorder(
        group_id=group_id,
        priorities=payload.priorities,
        actor_user_id=principal.user_id,
        request_id=ctx.request_id,
    )
