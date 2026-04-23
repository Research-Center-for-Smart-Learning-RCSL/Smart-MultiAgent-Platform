"""Guest-link enrollment endpoint (F.9).

The *public* URL is `https://<host>/g/{chatroom_id}/{guest_token}` (R13.05)
— that route is served by the frontend, which strips the token from the
browser history (R24.43) and then calls this backend endpoint:

    POST /api/guest/{chatroom_id}/{guest_token}/enroll

Once enrolled, subsequent requests go through the normal principal flow;
the guest's `chatroom_guests` row lets the room ACL check admit them.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.application.guest_service import GuestService
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/guest", tags=["guests"])


@router.post(
    "/{chatroom_id}/{guest_token}/enroll",
    status_code=204,
)
async def enroll_guest(
    chatroom_id: uuid.UUID = Path(...),
    guest_token: str = Path(..., min_length=16, max_length=128),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = GuestService(db)
    await service.enroll(
        chatroom_id=chatroom_id,
        token=guest_token,
        user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


__all__ = ["router"]
