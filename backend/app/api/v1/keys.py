"""`/api/keys/*` — individual key CRUD (§22.4, D.4).

Every handler is intentionally thin: extract → call application service →
map result. Plaintext never appears in a response body and never gets
logged (R7.03 / R7.15). The `secret` request field is `Field(repr=False)`
so a pydantic debug repr cannot surface it either.
"""

from __future__ import annotations

import uuid

from fastapi import Response, APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.application.key_service import KeyService
from contexts.keys.domain.models import ApiKey
from contexts.keys.domain.providers import ApiKeyProvider
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
    def from_domain(cls, key: ApiKey) -> "KeyOut":
        return cls(
            id=key.id,
            provider=key.provider.value,
            name=key.name,
            masked_preview=key.masked_preview,
            test_status=key.test_status.value,
            test_error=key.test_error,
            last_test_at=(
                key.last_test_at.isoformat() if key.last_test_at else None
            ),
            created_at=key.created_at.isoformat(),
        )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@router.get("", response_model=list[KeyOut])
async def list_my_keys(
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[KeyOut]:
    """List every key the caller owns (masked, no secrets)."""
    svc = KeyService(db)
    keys = await svc.list_owned(principal.user_id)
    return [KeyOut.from_domain(k) for k in keys]


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


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
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
