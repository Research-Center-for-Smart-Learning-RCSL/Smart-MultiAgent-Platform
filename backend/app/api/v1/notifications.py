"""`/api/notifications` — user notification endpoints (I.3, §22.12)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.notification.interfaces.facade import NotificationFacade
from shared_kernel.auth.dependencies import current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str | None
    metadata: dict
    read_at: str | None
    created_at: str


class MarkReadIn(BaseModel):
    # API-7: cap the batch so a giant `ids` list can't drive memory / DB load.
    ids: list[uuid.UUID] = Field(..., max_length=1000)


class MarkReadOut(BaseModel):
    marked: int


class UnreadCountOut(BaseModel):
    count: int


@router.get("")
async def list_notifications(
    cursor: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[NotificationOut]:
    facade = NotificationFacade(db)
    items = await facade.list_for_user(
        principal.user_id,
        cursor=cursor,
        limit=limit,
    )
    return [
        NotificationOut(
            id=n.id,
            kind=n.kind.value,
            title=n.title,
            body=n.body,
            metadata=n.metadata,
            read_at=n.read_at.isoformat() if n.read_at else None,
            created_at=n.created_at.isoformat(),
        )
        for n in items
    ]


@router.post("/read")
async def mark_read(
    body: MarkReadIn = Body(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> MarkReadOut:
    facade = NotificationFacade(db)
    count = await facade.mark_read(principal.user_id, body.ids)
    return MarkReadOut(marked=count)


@router.get("/unread-count")
async def unread_count(
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> UnreadCountOut:
    facade = NotificationFacade(db)
    count = await facade.unread_count(principal.user_id)
    return UnreadCountOut(count=count)


__all__ = ["router"]
