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

from contexts.conversation.application.access import reads_without_guest_row, resolve_room_access
from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    GuestAccessDisabled,
    GuestTokenInvalid,
)
from contexts.conversation.infrastructure.repositories import (
    ChatroomGuestRepository,
    ChatroomRepository,
)
from shared_kernel import audit
from shared_kernel.auth.permissions import Principal
from shared_kernel.labels import MAX_GUEST_LABEL, normalise_label


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
        principal: Principal,
        display_name: str | None = None,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> bool:
        """Enrol ``principal`` as a registered guest; ``True`` when a new row was written.

        The caller announces a written row to the room ([R13.19]): a new guest's
        kind reaches open clients only with the roster re-read, and without it
        their messages arrive unbadged ([R13.33]).
        """
        room = await self._rooms.get(chatroom_id)
        if room is None:
            raise ChatroomNotFound(str(chatroom_id))
        if not hmac.compare_digest(room.guest_token, token):
            raise GuestTokenInvalid(str(chatroom_id))
        if not room.allow_guest_links:
            # After the token check, so only a link holder learns the reason.
            raise GuestAccessDisabled(str(chatroom_id))
        if await self._reads_without_guest_row(principal, chatroom_id):
            # A member who opens the link with their account enters as themselves:
            # a row would let them pick a room label that wins over their account
            # name, unmarked, which is the guest-presents-as-member hole the guest
            # marker closes from the other side ([R13.33]).
            return False

        user_id = principal.user_id
        written = await self._guests.add(
            chatroom_id=chatroom_id,
            user_id=user_id,
            joined_via_token=token,
            # A guest label is self-chosen and wins the precedence over an account
            # display name when the turn engine builds a chat author label — so it
            # reaches the "Name: message" prefix in the message stream and, since
            # the room-owner note and the activity legend landed, the agent's
            # system prompt. Identity normalises account names for exactly this
            # reason; this path did not, which left the guarantee holding on the
            # branch nobody could reach and not on the one everybody could.
            display_name=normalise_label(display_name, max_len=MAX_GUEST_LABEL),
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
        return written

    async def _reads_without_guest_row(self, principal: Principal, chatroom_id: uuid.UUID) -> bool:
        access = await resolve_room_access(self._db, principal=principal, chatroom_id=chatroom_id)
        return reads_without_guest_row(access, is_admin=principal.is_admin)


__all__ = ["GuestService"]
