"""Assistant-session application service (§29 / R29.07-R29.09).

Runs in the web process: creates ephemeral sessions, enforces the message cap
and the per-user daily request quota synchronously (so an exhausted quota
returns 429 on the POST, AC-8), appends the user turn, and enqueues the arq
job that performs the provider call and streams tokens over WebSocket.
"""

from __future__ import annotations

import uuid

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.prompt_studio.application.config_service import ConfigService
from contexts.prompt_studio.domain.errors import (
    AssistantUnavailable,
    DailyQuotaExceeded,
    SessionLimitReached,
    SessionNotFound,
)
from contexts.prompt_studio.domain.models import AssistantSession, SessionMessage
from contexts.prompt_studio.infrastructure.session_store import SessionStore
from shared_kernel.auth.clients import get_redis, now
from shared_kernel.queue import enqueue


class SessionService:
    def __init__(self, db: AsyncSession, redis: Redis | None = None) -> None:
        self._db = db
        self._store = SessionStore(redis or get_redis())
        self._configs = ConfigService(db)

    async def create_session(self, *, user_id: uuid.UUID, project_id: uuid.UUID) -> AssistantSession:
        config = await self._configs.resolve_for_project(project_id=project_id, user_id=user_id)
        if config is None:
            raise AssistantUnavailable(str(project_id))
        return await self._store.create(user_id=user_id, project_id=project_id)

    async def post_message(
        self,
        *,
        session_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        content: str,
        editor_draft: str | None,
    ) -> None:
        session = await self.require_owned_session(session_id, actor_user_id)
        if self._store.at_message_cap(session):
            raise SessionLimitReached(str(session_id))

        config = await self._configs.resolve_for_project(project_id=session.project_id, user_id=actor_user_id)
        if config is None or config.key_id is None:
            raise AssistantUnavailable(str(session.project_id))

        day = now().strftime("%Y%m%d")
        used = await self._store.incr_daily_quota(config_id=config.id, user_id=actor_user_id, day=day)
        if used > config.daily_request_limit_per_user:
            raise DailyQuotaExceeded(str(actor_user_id))

        updated = await self._store.append_message(session_id, SessionMessage(role="user", content=content))
        if updated is None:
            raise SessionNotFound(str(session_id))
        await enqueue(
            "prompt_assistant_turn",
            str(session_id),
            editor_draft or "",
            _job_id=f"prompt:{session_id}:{len(updated.messages)}",
        )

    async def require_owned_session(
        self, session_id: uuid.UUID, actor_user_id: uuid.UUID
    ) -> AssistantSession:
        """Fetch the session, verifying ownership. Public: also called by the WS route via the facade.

        A mismatched owner reads as not-found — never leak another user's session.
        """
        session = await self._store.get(session_id)
        if session is None or session.user_id != actor_user_id:
            raise SessionNotFound(str(session_id))
        return session


__all__ = ["SessionService"]
