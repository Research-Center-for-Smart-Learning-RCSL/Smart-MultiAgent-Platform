"""`/api/keys/*` — individual key CRUD (§22.4, D.4).

Every handler is intentionally thin: extract → call application service →
map result. Plaintext never appears in a response body and never gets
logged (R7.03 / R7.15). The `secret` request field is `Field(repr=False)`
so a pydantic debug repr cannot surface it either.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.keys.application.key_service import KeyService
from contexts.keys.domain.models import ApiKey
from contexts.keys.domain.providers import ApiKeyProvider
from contexts.keys.interfaces.facade import KeysFacade
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/keys", tags=["keys"])


# ---------------------------------------------------------------------------
# Schemas — nothing here ever carries plaintext out.
# ---------------------------------------------------------------------------


class KeyUploadIn(BaseModel):
    provider: ApiKeyProvider
    name: str = Field(min_length=1, max_length=200)
    # `repr=False` blocks accidental leak through a pydantic error message or
    # a debug log that dumps the model.
    secret: str = Field(min_length=1, max_length=4096, repr=False)


class KeyOut(BaseModel):
    id: uuid.UUID
    provider: str
    name: str
    masked_preview: str
    test_status: str
    test_error: str | None
    last_test_at: str | None
    created_at: str

    @classmethod
    def from_domain(cls, key: ApiKey) -> KeyOut:
        return cls(
            id=key.id,
            provider=key.provider.value,
            name=key.name,
            masked_preview=key.masked_preview,
            test_status=key.test_status.value,
            test_error=key.test_error,
            last_test_at=(key.last_test_at.isoformat() if key.last_test_at else None),
            created_at=key.created_at.isoformat(),
        )


class KeyListOut(KeyOut):
    """`KeyOut` plus the active-carry count. Exposed only on the my-keys list so
    the shared `KeyOut` (also returned by the project-carried surface) stays free
    of a field that has no meaning there."""

    project_count: int

    @classmethod
    def with_count(cls, key: ApiKey, *, project_count: int) -> KeyListOut:
        return cls(**KeyOut.from_domain(key).model_dump(), project_count=project_count)


class KeyProjectOut(BaseModel):
    """One project a key is carried into, with its binding footprint."""

    project_id: uuid.UUID
    project_name: str
    carried_at: str
    group_count: int
    agent_count: int


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@router.get("", response_model=list[KeyListOut])
async def list_my_keys(
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[KeyListOut]:
    """List every key the caller owns (masked, no secrets).

    The `project_count` badge counts only carries the caller can still see —
    project not soft-deleted AND caller still a member — so it matches the
    per-key detail view's project list, which applies the same filter
    (otherwise a stale carry into a left/deleted project inflates the badge).
    """
    svc = KeyService(db)
    keys = await svc.list_owned(principal.user_id, limit=pagination.limit, offset=pagination.offset)
    proj_ids_by_key = await KeysFacade(db).carried_project_ids_for_keys([k.id for k in keys])
    all_ids = list({pid for ids in proj_ids_by_key.values() for pid in ids})
    tenancy = TenancyFacade(db)
    member_ids = await tenancy.member_project_ids(principal.user_id, all_ids)
    existing = await tenancy.get_projects(all_ids)
    visible = member_ids & existing.keys()
    return [
        KeyListOut.with_count(
            k,
            project_count=sum(1 for pid in proj_ids_by_key.get(k.id, []) if pid in visible),
        )
        for k in keys
    ]


@router.get("/{key_id}", response_model=KeyOut)
async def get_my_key(
    key_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> KeyOut:
    """Fetch a single key the caller owns (for the detail view).

    Returns 404 for a missing OR non-owned key so it isn't a cross-user
    key-id enumeration oracle. Resolving by id avoids the detail view's old
    'scan the first page of /keys' approach, which 404'd keys past page 1.
    """
    key = await KeyService(db).get_owned(key_id, principal.user_id)
    return KeyOut.from_domain(key)


@router.get("/{key_id}/projects", response_model=list[KeyProjectOut])
async def list_key_projects(
    key_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[KeyProjectOut]:
    """Reverse view: which projects this key is carried into (owner-only).

    Ownership is enforced inside `KeysFacade.projects_for_key`. Results are
    filtered to projects the caller is still a member of — a key owner who has
    left a project must not see that project's data even if the carry revocation
    fanout had not yet run (R7.03 / CLAUDE.md AuthZ rule).
    """
    usages = await KeysFacade(db).projects_for_key(key_id=key_id, caller_user_id=principal.user_id)
    project_ids = [u.project_id for u in usages]
    all_group_ids = [gid for u in usages for gid in u.group_ids]
    agent_counts = await AgentsFacade(db).count_agents_for_key_groups(all_group_ids)
    tenancy = TenancyFacade(db)
    # Batch both per-project lookups into one query each (no N+1): membership
    # filter first (a key owner who has left a project must not see its data,
    # even if the carry-revocation fanout has not yet run), then names.
    member_ids = await tenancy.member_project_ids(principal.user_id, project_ids)
    projects = await tenancy.get_projects(project_ids)
    out: list[KeyProjectOut] = []
    for u in usages:
        if u.project_id not in member_ids:
            continue
        project = projects.get(u.project_id)
        if project is None:
            continue
        out.append(
            KeyProjectOut(
                project_id=u.project_id,
                project_name=project.name,
                carried_at=u.carried_at.isoformat(),
                group_count=len(u.group_ids),
                agent_count=sum(agent_counts.get(gid, 0) for gid in u.group_ids),
            )
        )
    return out


@router.post("", response_model=KeyOut, status_code=status.HTTP_201_CREATED)
async def upload_key(
    payload: KeyUploadIn,
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> KeyOut:
    """Upload a new provider key (§7.2 flow).

    AuthZ: KEY_UPLOAD is granted to any role carrying a user scope (§5.2 #2).
    There is no path-param scope here; we still run the decision through the
    matrix so admin-bypass + email-verification policy apply uniformly. The
    matrix row accepts any non-guest role, so we only need the principal to
    have *some* role — i.e. be a logged-in user. The `current_principal`
    dependency already enforces that.
    """
    svc = KeyService(db)
    result = await svc.upload(
        owner_user_id=principal.user_id,
        provider=payload.provider,
        name=payload.name,
        secret=payload.secret,
        actor_ip=str(ctx.actor_ip) if ctx.actor_ip else None,
        request_id=ctx.request_id,
    )
    return KeyOut.from_domain(result.key)


@router.post("/{key_id}/retest", response_model=KeyOut)
async def retest_key(
    key_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> KeyOut:
    """Re-run the provider probe against the stored key."""
    svc = KeyService(db)
    refreshed = await svc.retest(
        key_id=key_id,
        caller_user_id=principal.user_id,
        actor_ip=str(ctx.actor_ip) if ctx.actor_ip else None,
        request_id=ctx.request_id,
    )
    return KeyOut.from_domain(refreshed)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_key(
    key_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    """Soft-delete a key. Cascades to Key-Group membership via ON DELETE."""
    # Ownership is enforced inside the service (matrix cap #3 is OWN_ONLY, so
    # the scope-based `require(...)` dependency would be redundant with the
    # service check; we keep a single source of truth to avoid divergence).
    svc = KeyService(db)
    await svc.delete(
        key_id=key_id,
        caller_user_id=principal.user_id,
        actor_ip=str(ctx.actor_ip) if ctx.actor_ip else None,
        request_id=ctx.request_id,
    )
