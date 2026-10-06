"""Anonymous guest session lifecycle (R5.04, R13.06, R13.06a, R13.06b).

Creates chatroom-scoped guest sessions backed by ``guest_sessions`` rows,
without touching the ``users`` table. The guest token in the URL serves as
the authentication credential; the server issues a chatroom-scoped JWT.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.conversation.application.access import ensure_parents_live, ensure_room_live
from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    GuestAccessDisabled,
    GuestBanNotFound,
    GuestCapReached,
    GuestDisplayNameInvalid,
    GuestRemoved,
    GuestSessionNotFound,
    GuestTokenInvalid,
)
from contexts.conversation.domain.models import Chatroom, GuestBan
from contexts.conversation.infrastructure.repositories import (
    ChatroomRepository,
    GuestBanRepository,
    GuestSessionRepository,
)
from shared_kernel import audit
from shared_kernel.auth import tokens as token_utils
from shared_kernel.auth.clients import now
from shared_kernel.auth.jwt import sign_guest_token
from shared_kernel.db.advisory_lock import advisory_xact_lock
from shared_kernel.labels import MAX_GUEST_LABEL, normalise_label

_ACTIVE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class GuestSessionResult:
    access_token: str
    refresh_token: str
    guest_session_id: uuid.UUID
    display_name: str
    is_resuming: bool
    # A new session, or a resume that changed the stored name: other viewers'
    # rosters are now stale. False for a plain resume, which changes nothing they
    # can see -- the route emits only on True, since it is public.
    roster_changed: bool = False


@dataclass(frozen=True, slots=True)
class GuestRenameResult:
    display_name: str
    changed: bool


@dataclass(frozen=True, slots=True)
class GuestRefreshResult:
    access_token: str
    refresh_token: str
    guest_session_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class GuestRemovalResult:
    # False for a repeat of a removal already in force: the route then emits no
    # event, so an idempotent retry does not re-announce it.
    changed: bool


class GuestSessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._rooms = ChatroomRepository(db)
        self._sessions = GuestSessionRepository(db)
        self._bans = GuestBanRepository(db)

    async def ensure_admits_guests(self, chatroom_id: uuid.UUID) -> None:
        """Raise unless the room is live and its guest links are on."""
        room = await ensure_room_live(self._db, chatroom_id)
        if not room.allow_guest_links:
            raise GuestAccessDisabled(str(chatroom_id))

    async def _audit(
        self,
        action: str,
        *,
        guest_session_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        remote_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> None:
        # `guest_session_id` stays in metadata for queries written against the
        # earlier shape, whose actor was NULL (audit_logs is append-only).
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                actor_user_id=guest_session_id,
                actor_ip=remote_ip,
                resource_type="chatroom",
                resource_id=chatroom_id,
                metadata={"guest_session_id": str(guest_session_id)},
                request_id=request_id,
                actor_guest_room_id=chatroom_id,
            ),
        )

    async def update_display_name(
        self,
        *,
        guest_session_id: uuid.UUID,
        display_name: str,
        remote_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> GuestRenameResult:
        """Validate and persist a new display name; returns the stored (normalised)
        name and whether it differs from the one it replaced."""
        normalised = normalise_label(display_name, max_len=MAX_GUEST_LABEL)
        if normalised is None:
            raise GuestDisplayNameInvalid(str(guest_session_id))
        display_name = normalised
        session = await self._sessions.find_by_id(guest_session_id)
        if session is None:
            raise GuestTokenInvalid(str(guest_session_id))
        changed = session.display_name != display_name
        if changed:
            await self._sessions.update_display_name(guest_session_id, display_name)
            await self._audit(
                "guest.session.renamed",
                guest_session_id=guest_session_id,
                chatroom_id=session.chatroom_id,
                remote_ip=remote_ip,
                request_id=request_id,
            )
        return GuestRenameResult(display_name=display_name, changed=changed)

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
        # A missing room answers as a wrong link does, so the endpoint cannot be
        # used to tell existing room ids from absent ones. Past the link check the
        # caller holds the token and may learn why it no longer works ([R13.32]).
        # Bytes, because compare_digest raises on a non-ASCII str: a 500 for an
        # existing room against a 404 for a missing one would reopen that oracle.
        if room is None or not hmac.compare_digest(room.guest_token.encode(), guest_token.encode()):
            raise GuestTokenInvalid(str(chatroom_id))
        await ensure_parents_live(self._db, room)
        if not room.allow_guest_links:
            raise GuestAccessDisabled(str(chatroom_id))

        normalised = normalise_label(display_name, max_len=MAX_GUEST_LABEL)
        if normalised is None:
            raise GuestDisplayNameInvalid(str(chatroom_id))
        display_name = normalised

        # Serialises joins per room for the rest of the transaction ([R13.06a]). It
        # is taken before the browser lookup too: two first joins from one browser
        # would otherwise both miss and both insert. A row lock on existing
        # sessions locks nothing in an empty room, which is the case that matters.
        await advisory_xact_lock(self._db, _join_lock_key(chatroom_id))

        if browser_id:
            banned = await self._bans.is_banned(
                chatroom_id=chatroom_id, browser_id_hash=browser_id_hash(browser_id)
            )
            if banned:
                raise GuestRemoved(str(chatroom_id))
            # A removed but unbanned session is not found here, so its browser
            # rejoins as a new session ([R13.07a]).
            existing = await self._sessions.find_by_browser_id(chatroom_id=chatroom_id, browser_id=browser_id)
            if existing:
                renamed = existing.display_name != display_name
                if renamed:
                    await self._sessions.update_display_name(existing.id, display_name)
                await self._sessions.update_last_seen(existing.id)

                refresh_token = token_utils.new_refresh_token()
                await self._sessions.update_refresh_hash(existing.id, token_utils.hash_refresh(refresh_token))

                jwt_token, _ = sign_guest_token(
                    guest_session_id=existing.id,
                    chatroom_id=chatroom_id,
                    display_name=display_name,
                )

                await self._audit(
                    "guest.session.resumed",
                    guest_session_id=existing.id,
                    chatroom_id=chatroom_id,
                    remote_ip=remote_ip,
                    request_id=request_id,
                )

                return GuestSessionResult(
                    access_token=jwt_token,
                    refresh_token=refresh_token,
                    guest_session_id=existing.id,
                    display_name=display_name,
                    is_resuming=True,
                    roster_changed=renamed,
                )

        settings = get_settings()
        since = now() - _ACTIVE_WINDOW
        active_count = await self._sessions.count_active(chatroom_id, since=since)
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

        await self._audit(
            "guest.session.created",
            guest_session_id=session.id,
            chatroom_id=chatroom_id,
            remote_ip=remote_ip,
            request_id=request_id,
        )

        return GuestSessionResult(
            access_token=jwt_token,
            refresh_token=refresh_token,
            guest_session_id=session.id,
            display_name=display_name,
            is_resuming=False,
            roster_changed=True,
        )

    async def refresh(
        self,
        *,
        chatroom_id: uuid.UUID,
        refresh_token: str,
        remote_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> GuestRefreshResult:
        # The cookie is matched first: the route admits any non-empty cookie, so
        # naming a dead room or "disabled" first would tell an arbitrary caller the
        # room's state. The rotation is that match; the checks after it run in the
        # same transaction, so their refusal rolls the rotation back (Q-6).
        new_refresh = token_utils.new_refresh_token()
        session = await self._sessions.rotate_refresh(
            old_hash=token_utils.hash_refresh(refresh_token),
            new_hash=token_utils.hash_refresh(new_refresh),
            chatroom_id=chatroom_id,
        )
        if session is None:
            raise GuestTokenInvalid(str(chatroom_id))
        room = await ensure_room_live(self._db, chatroom_id)
        if session.revoked_at is not None:
            raise GuestRemoved(str(chatroom_id))
        if not room.allow_guest_links:
            raise GuestAccessDisabled(str(chatroom_id))

        jwt_token, _ = sign_guest_token(
            guest_session_id=session.id,
            chatroom_id=chatroom_id,
            display_name=session.display_name,
        )

        await self._audit(
            "guest.session.refreshed",
            guest_session_id=session.id,
            chatroom_id=chatroom_id,
            remote_ip=remote_ip,
            request_id=request_id,
        )

        return GuestRefreshResult(
            access_token=jwt_token,
            refresh_token=new_refresh,
            guest_session_id=session.id,
        )

    # -- Moderation ([R13.07a], [R6.12]). Callers gate on matrix row 18. --

    async def _audit_moderation(
        self,
        action: str,
        *,
        actor_user_id: uuid.UUID,
        resource_type: str,
        resource_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        remote_ip: str | None,
        request_id: uuid.UUID | None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                actor_user_id=actor_user_id,
                actor_ip=remote_ip,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata={"chatroom_id": str(chatroom_id), **(metadata or {})},
                request_id=request_id,
            ),
        )

    async def remove(
        self,
        *,
        chatroom_id: uuid.UUID,
        guest_session_id: uuid.UUID,
        ban: bool,
        actor_user_id: uuid.UUID,
        remote_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> GuestRemovalResult:
        """End a guest session and, with ``ban``, refuse its browser re-entry.

        Idempotent: removing a removed session changes nothing, and banning it
        afterwards only adds the ban. The row is kept so its name still labels
        the guest's messages ([R13.33]).
        """
        session = await self._sessions.find_by_id(guest_session_id)
        if session is None or session.chatroom_id != chatroom_id:
            raise GuestSessionNotFound(str(guest_session_id))
        # The join lock, so a resume from the same browser cannot slip between
        # the revoke and the ban and come back with a live session.
        await advisory_xact_lock(self._db, _join_lock_key(chatroom_id))

        async def audit_on_session(action: str, metadata: dict[str, str] | None = None) -> None:
            await self._audit_moderation(
                action,
                actor_user_id=actor_user_id,
                resource_type="guest_session",
                resource_id=guest_session_id,
                chatroom_id=chatroom_id,
                remote_ip=remote_ip,
                request_id=request_id,
                metadata=metadata,
            )

        removed = await self._sessions.revoke(guest_session_id)
        if removed:
            await audit_on_session("guest.session.removed")
        banned = None
        if ban:
            banned = await self._bans.create(
                chatroom_id=chatroom_id,
                guest_session_id=guest_session_id,
                browser_id_hash=browser_id_hash(session.browser_id) if session.browser_id else None,
                display_name=session.display_name,
                created_by=actor_user_id,
            )
            if banned is not None:
                await audit_on_session("guest.banned", {"ban_id": str(banned.id)})
        return GuestRemovalResult(changed=removed or banned is not None)

    async def list_bans(self, chatroom_id: uuid.UUID) -> list[GuestBan]:
        return await self._bans.list_for_room(chatroom_id)

    async def unban(
        self,
        *,
        chatroom_id: uuid.UUID,
        ban_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        remote_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        lifted = await self._bans.delete(chatroom_id=chatroom_id, ban_id=ban_id)
        if lifted is None:
            raise GuestBanNotFound(str(ban_id))
        await self._audit_moderation(
            "guest.unbanned",
            actor_user_id=actor_user_id,
            resource_type="guest_session",
            resource_id=lifted.guest_session_id,
            chatroom_id=chatroom_id,
            remote_ip=remote_ip,
            request_id=request_id,
            metadata={"ban_id": str(ban_id)},
        )

    async def rotate_link(
        self,
        *,
        chatroom_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        remote_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> Chatroom:
        """Replace the room's guest link token. Joined guests keep their sessions:
        refresh rides the session cookie, never the link ([R6.12])."""
        room = await self._rooms.rotate_guest_token(chatroom_id)
        if room is None:
            raise ChatroomNotFound(str(chatroom_id))
        await self._audit_moderation(
            "chatroom.guest_link.rotated",
            actor_user_id=actor_user_id,
            resource_type="chatroom",
            resource_id=chatroom_id,
            chatroom_id=chatroom_id,
            remote_ip=remote_ip,
            request_id=request_id,
        )
        return room


def browser_id_hash(browser_id: str) -> str:
    """What a ban stores in place of the browser id (spec §8, privacy)."""
    return hashlib.sha256(browser_id.encode()).hexdigest()


def _join_lock_key(chatroom_id: uuid.UUID) -> str:
    return f"guest-join:{chatroom_id}"


__all__ = [
    "GuestRefreshResult",
    "GuestRemovalResult",
    "GuestSessionResult",
    "GuestSessionService",
    "browser_id_hash",
]
