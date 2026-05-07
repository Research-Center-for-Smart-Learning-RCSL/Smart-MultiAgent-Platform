"""`/api/projects/{pid}/search-keys/*` (§22.9a, D.11)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.application.search_service import SearchKeyService
from contexts.keys.domain.search import SearchKey, SearchProvider
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

router = APIRouter(prefix="/api/projects", tags=["search-keys"])


class SearchKeyIn(BaseModel):
    provider: SearchProvider
    secret: str = Field(min_length=1, max_length=4096, repr=False)
    config: dict[str, Any] = Field(default_factory=dict)


class SearchKeyOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    provider: str
    masked_preview: str
    test_status: str
    test_error: str | None
    last_test_at: str | None
    is_active: bool
    config: dict[str, Any]
    created_at: str

    @classmethod
    def from_domain(cls, sk: SearchKey) -> "SearchKeyOut":
        return cls(
            id=sk.id, project_id=sk.project_id,
            provider=sk.provider.value,
            masked_preview=sk.masked_preview,
            test_status=sk.test_status.value,
            test_error=sk.test_error,
            last_test_at=sk.last_test_at.isoformat() if sk.last_test_at else None,
            is_active=sk.is_active,
            config=sk.config,
            created_at=sk.created_at.isoformat(),
        )


@router.get(
    "/{project_id}/search-keys",
    response_model=list[SearchKeyOut],
    dependencies=[Depends(require_membership(project_param="project_id"))],
)
async def list_search_keys(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(db_session),
) -> list[SearchKeyOut]:
    svc = SearchKeyService(db)
    return [
        SearchKeyOut.from_domain(sk)
        for sk in await svc.list_for_project(project_id)
    ]


@router.post(
    "/{project_id}/search-keys",
    response_model=SearchKeyOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require(Capability.KEY_CONFIGURE, scope_from_path(project_param="project_id"))),
    ],
)
async def upload_search_key(
    project_id: uuid.UUID,
    payload: SearchKeyIn,
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> SearchKeyOut:
    svc = SearchKeyService(db)
    sk = await svc.upload(
        project_id=project_id,
        provider=payload.provider,
        secret=payload.secret,
        config=payload.config,
        actor_user_id=principal.user_id,
        request_id=ctx.request_id,
    )
    return SearchKeyOut.from_domain(sk)


@router.post(
    "/{project_id}/search-keys/{key_id}/retest",
    response_model=SearchKeyOut,
    dependencies=[
        Depends(require(Capability.KEY_CONFIGURE, scope_from_path(project_param="project_id"))),
    ],
)
async def retest_search_key(
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> SearchKeyOut:
    svc = SearchKeyService(db)
    return SearchKeyOut.from_domain(
        await svc.retest(
            project_id=project_id, key_id=key_id,
            actor_user_id=principal.user_id, request_id=ctx.request_id,
        )
    )


@router.post(
    "/{project_id}/search-keys/{key_id}/activate",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[
        Depends(require(Capability.KEY_CONFIGURE, scope_from_path(project_param="project_id"))),
    ],
)
async def activate_search_key(
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    await SearchKeyService(db).activate(
        project_id=project_id,
        key_id=key_id,
        actor_user_id=principal.user_id,
        request_id=ctx.request_id,
    )


@router.delete(
    "/{project_id}/search-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[
        Depends(require(Capability.KEY_CONFIGURE, scope_from_path(project_param="project_id"))),
    ],
)
async def delete_search_key(
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    await SearchKeyService(db).delete(
        project_id=project_id,
        key_id=key_id,
        actor_user_id=principal.user_id,
        request_id=ctx.request_id,
    )
