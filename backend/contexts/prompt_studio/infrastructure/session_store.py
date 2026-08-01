"""Redis-backed ephemeral assistant sessions + per-user daily quota (§29).

Sessions are a TTL-bounded JSON blob (2 h) holding the message history; there
is no server-side persistence beyond the live session (R29.07). The daily
request quota is a per-user, per-config INCR counter that expires after a day.

SoC: infrastructure only — no domain imports beyond the session dataclasses.
"""

from __future__ import annotations

import json
import uuid

from redis.asyncio import Redis

from contexts.prompt_studio.domain.models import (
    SESSION_MAX_MESSAGES,
    SESSION_TTL_SECONDS,
    AssistantSession,
    SessionMessage,
)

_SESSION_PREFIX = "ws:prompt:session:"
_QUOTA_PREFIX = "prompt:quota:"
_DAY_SECONDS = 24 * 60 * 60


def _session_key(session_id: uuid.UUID) -> str:
    return f"{_SESSION_PREFIX}{session_id}"


def _quota_key(config_id: uuid.UUID, user_id: uuid.UUID, day: str) -> str:
    return f"{_QUOTA_PREFIX}{config_id}:{user_id}:{day}"


class SessionStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def create(self, *, user_id: uuid.UUID, project_id: uuid.UUID) -> AssistantSession:
        session = AssistantSession(
            session_id=uuid.uuid4(), user_id=user_id, project_id=project_id, messages=()
        )
        await self._save(session)
        return session

    async def get(self, session_id: uuid.UUID) -> AssistantSession | None:
        raw = await self._redis.get(_session_key(session_id))
        if raw is None:
            return None
        data = json.loads(raw)
        return AssistantSession(
            session_id=session_id,
            user_id=uuid.UUID(data["user_id"]),
            project_id=uuid.UUID(data["project_id"]),
            messages=tuple(
                SessionMessage(role=m["role"], content=m["content"], error=m.get("error", False))
                for m in data["messages"]
            ),
        )

    async def append_message(self, session_id: uuid.UUID, message: SessionMessage) -> AssistantSession | None:
        """Re-fetch the session, append, and persist (TTL refreshed). Caller enforces caps.

        Takes the session id rather than a caller-held snapshot: this is a
        read-modify-write against Redis, so appending must start from the
        latest stored state, not a copy read earlier by the caller (e.g. a
        worker that read the session before streaming a reply must not
        clobber a message the user posted while that reply was streaming).
        Returns None if the session expired/was removed since the caller last
        saw it.
        """
        session = await self.get(session_id)
        if session is None:
            return None
        updated = AssistantSession(
            session_id=session.session_id,
            user_id=session.user_id,
            project_id=session.project_id,
            messages=(*session.messages, message),
        )
        await self._save(updated)
        return updated

    @staticmethod
    def at_message_cap(session: AssistantSession) -> bool:
        return len(session.messages) >= SESSION_MAX_MESSAGES

    async def _save(self, session: AssistantSession) -> None:
        payload = json.dumps(
            {
                "user_id": str(session.user_id),
                "project_id": str(session.project_id),
                "messages": [
                    {"role": m.role, "content": m.content, "error": m.error} for m in session.messages
                ],
            },
            separators=(",", ":"),
        )
        await self._redis.set(_session_key(session.session_id), payload, ex=SESSION_TTL_SECONDS)

    # -- daily request quota (R29.09) ----------------------------------------

    async def incr_daily_quota(self, *, config_id: uuid.UUID, user_id: uuid.UUID, day: str) -> int:
        """Increment and return the per-user, per-config counter for *day*."""
        key = _quota_key(config_id, user_id, day)
        pipe = self._redis.pipeline(transaction=False)
        pipe.incr(key)
        pipe.expire(key, _DAY_SECONDS)
        count, _ = await pipe.execute()
        return int(count)

    async def decr_daily_quota(self, *, config_id: uuid.UUID, user_id: uuid.UUID, day: str) -> int:
        """Refund one unit of the daily counter.

        Used when the web process charged the quota optimistically but the
        worker then found the turn genuinely couldn't run (config/key
        vanished, or no model could be resolved) — the user made no
        effective use of a request, so it shouldn't count against their cap.
        """
        key = _quota_key(config_id, user_id, day)
        return int(await self._redis.decr(key))


__all__ = ["SessionStore"]
