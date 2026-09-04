"""Anonymous guest session lifecycle (R5.04, R13.06, R13.06a, R13.06b).

Creates chatroom-scoped guest sessions backed by ``guest_sessions`` rows,
without touching the ``users`` table. The guest token in the URL serves as
the authentication credential; the server issues a chatroom-scoped JWT.
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    GuestCapReached,
    GuestTokenInvalid,
)
from contexts.conversation.infrastructure.repositories import (
    ChatroomRepository,
    GuestSessionRepository,
)
from shared_kernel import audit
from shared_kernel.auth import tokens as token_utils
from shared_kernel.auth.clients import now
from shared_kernel.auth.jwt import sign_guest_token
from shared_kernel.labels import MAX_GUEST_LABEL, normalise_label

_ACTIVE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class GuestSessionResult:
    access_token: str
    refresh_token: str
    guest_session_id: uuid.UUID
    display_name: str
    is_resuming: bool


@dataclass(frozen=True, slots=True)
class GuestRefreshResult:
    access_token: str
    refresh_token: str
    guest_session_id: uuid.UUID


class GuestSessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._rooms = ChatroomRepository(db)
        self._sessions = GuestSessionRepository(db)

    async def update_display_name(
        self,
        *,
        guest_session_id: uuid.UUID,
        display_name: str,
    ) -> str:
        """Validate and persist a new display name. Returns the normalised name."""
        normalised = normalise_label(display_name, max_len=MAX_GUEST_LABEL)
        if normalised is None:
            raise GuestTokenInvalid(str(guest_session_id))
        display_name = normalised
        session = await self._sessions.find_by_id(guest_session_id)
        if session is None:
            raise GuestTokenInvalid(str(guest_session_id))
        await self._sessions.update_display_name(guest_session_id, display_name)
        return display_name

    async def create_or_resume(
        self,
        *,
        chatroom_id: uuid.UUID,
        guest_token: str,
        display_name: str,
        browser_id: str | None = None,
        remote_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> GuestSessionResult:
        room = await self._rooms.get(chatroom_id)
        if room is None:
            raise ChatroomNotFound(str(chatroom_id))
        if not hmac.compare_digest(room.guest_token, guest_token):
            raise GuestTokenInvalid(str(chatroom_id))
        if not room.allow_guest_links:
            raise GuestTokenInvalid(str(chatroom_id))

        normalised = normalise_label(display_name, max_len=MAX_GUEST_LABEL)
        if normalised is None:
            raise GuestTokenInvalid(str(chatroom_id))
        display_name = normalised

        if browser_id:
            existing = await self._sessions.find_by_browser_id(chatroom_id=chatroom_id, browser_id=browser_id)
            if existing:
                if existing.display_name != display_name:
                    await self._sessions.update_display_name(existing.id, display_name)
                await self._sessions.update_last_seen(existing.id)

                refresh_token = token_utils.new_refresh_token()
                await self._sessions.update_refresh_hash(existing.id, token_utils.hash_refresh(refresh_token))

                jwt_token, _ = sign_guest_token(
                    guest_session_id=existing.id,
                    chatroom_id=chatroom_id,
                    display_name=display_name,
                )

                await audit.emit(
                    self._db,
                    audit.AuditEvent(
                        action="guest.session.resumed",
                        actor_user_id=existing.id,
                        actor_ip=remote_ip,
                        resource_type="chatroom",
                        resource_id=chatroom_id,
                        metadata={"guest": True, "chatroom_id": str(chatroom_id)},
                        request_id=request_id,
                    ),
                )

                return GuestSessionResult(
                    access_token=jwt_token,
                    refresh_token=refresh_token,
                    guest_session_id=existing.id,
                    display_name=display_name,
                    is_resuming=True,
                )

        settings = get_settings()
        since = now() - _ACTIVE_WINDOW
        active_count = await self._sessions.count_active_for_update(chatroom_id, since=since)
        if active_count >= settings.limits.max_guests_per_chatroom:
            raise GuestCapReached(str(chatroom_id))

        refresh_token = token_utils.new_refresh_token()
        session = await self._sessions.create(
            chatroom_id=chatroom_id,
            display_name=display_name,
            browser_id=browser_id,
            refresh_token_hash=token_utils.hash_refresh(refresh_token),
        )

        jwt_token, _ = sign_guest_token(
            guest_session_id=session.id,
            chatroom_id=chatroom_id,
            display_name=display_name,
        )

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="guest.session.created",
                actor_user_id=session.id,
                actor_ip=remote_ip,
                resource_type="chatroom",
                resource_id=chatroom_id,
                metadata={"guest": True, "chatroom_id": str(chatroom_id)},
                request_id=request_id,
            ),
        )

        return GuestSessionResult(
            access_token=jwt_token,
            refresh_token=refresh_token,
            guest_session_id=session.id,
            display_name=display_name,
            is_resuming=False,
        )

    async def refresh(
        self,
        *,
        chatroom_id: uuid.UUID,
        refresh_token: str,
    ) -> GuestRefreshResult:
        room = await self._rooms.get(chatroom_id)
        if room is None:
            raise ChatroomNotFound(str(chatroom_id))
        if not room.allow_guest_links:
            raise GuestTokenInvalid(str(chatroom_id))

        token_hash = token_utils.hash_refresh(refresh_token)
        session = await self._sessions.find_by_refresh_hash(refresh_token_hash=token_hash)
        if session is None or session.chatroom_id != chatroom_id:
            raise GuestTokenInvalid(str(chatroom_id))

        new_refresh = token_utils.new_refresh_token()
        await self._sessions.update_refresh_hash(session.id, token_utils.hash_refresh(new_refresh))

        jwt_token, _ = sign_guest_token(
            guest_session_id=session.id,
            chatroom_id=chatroom_id,
            display_name=session.display_name,
        )

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="guest.session.refreshed",
                actor_user_id=session.id,
                resource_type="chatroom",
                resource_id=chatroom_id,
                metadata={"guest": True, "chatroom_id": str(chatroom_id)},
            ),
        )

        return GuestRefreshResult(
            access_token=jwt_token,
            refresh_token=new_refresh,
            guest_session_id=session.id,
        )


__all__ = ["GuestRefreshResult", "GuestSessionResult", "GuestSessionService"]
