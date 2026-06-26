"""Guest-link enrollment (F.9, R6.11 / R13.05–R13.07).

Guests are regular registered users (R5.04); the link does not create a
user — it records a `chatroom_guests` row against the already-signed-up
user's id. Endpoints calling this service MUST ensure:

  - the principal is authenticated (email-verified is NOT required — room
    membership is gated by `chatroom_guests` + the room ACL flags, and
    guest rooms are low-trust by definition),
  - the URL token matches `chatrooms.guest_token`.

The token is not a secret per R13.07 (it's a room-scoped identifier);
still, we compare in constant time to avoid side-channels.
"""

from __future__ import annotations

import hmac
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    GuestTokenInvalid,
)
from contexts.conversation.domain.models import Chatroom
from contexts.conversation.infrastructure.repositories import (
    ChatroomGuestRepository,
    ChatroomRepository,
)
from shared_kernel import audit


class GuestService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._rooms = ChatroomRepository(db)
        self._guests = ChatroomGuestRepository(db)

    async def enroll(
        self,
        *,
        chatroom_id: uuid.UUID,
        token: str,
        user_id: uuid.UUID,
        display_name: str | None = None,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> Chatroom:
        room = await self._rooms.get(chatroom_id)
        if room is None:
            raise ChatroomNotFound(str(chatroom_id))
        if not hmac.compare_digest(room.guest_token, token):
            raise GuestTokenInvalid(str(chatroom_id))
        if not room.allow_guest_links:
            # Room is not accepting guests — treat as invalid token to
            # avoid leaking the fact that the room exists.
            raise GuestTokenInvalid(str(chatroom_id))

        await self._guests.add(
            chatroom_id=chatroom_id,
            user_id=user_id,
            joined_via_token=token,
            display_name=display_name,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="guest.joined",
                actor_user_id=user_id,
                actor_ip=actor_ip,
                resource_type="chatroom",
                resource_id=chatroom_id,
                metadata={"joined_via_token": token[:8] + "…"},
                request_id=request_id,
            ),
        )
        return room


__all__ = ["GuestService"]
