"""`/api/chatrooms/{id}/search` — room-scoped full-text search (F.10)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.application.access import (
    ensure_can_read,
    resolve_room_access,
)
from contexts.conversation.application.message_service import MessageService
from shared_kernel.auth.dependencies import current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/chatrooms", tags=["search"])


class SearchHit(BaseModel):
    message_id: uuid.UUID
    sender_type: str
    sender_id: uuid.UUID | None
    created_at: str
    snippet: str
    rank: float


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


@router.get("/{chatroom_id}/search")
async def search_messages(
    chatroom_id: uuid.UUID = Path(...),
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10_000),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SearchResponse:
    access = await resolve_room_access(
        db,
        principal=principal,
        chatroom_id=chatroom_id,
    )
    ensure_can_read(access, is_admin=principal.is_admin)
    service = MessageService(db)
    results = await service.search(
        chatroom_id=chatroom_id,
        query=q,
        limit=limit,
        offset=offset,
    )
    hits = [
        SearchHit(
            message_id=m.id,
            sender_type=m.sender_type.value,
            sender_id=m.sender_id,
            created_at=m.created_at.isoformat() if m.created_at else "",
            snippet=snippet,
            rank=rank,
        )
        for m, rank, snippet in results
    ]
    return SearchResponse(query=q, hits=hits)


__all__ = ["router"]
