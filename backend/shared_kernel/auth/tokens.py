"""Refresh-token session store + access-token jti denylist (R6.03, R6.06, R6.08).

Two Redis keyspaces:

  session:{sha256(refresh_token)}     HSET  { user_id, session_id, family_id,
                                              last_jti, issued_at, expires_at }
                                      TTL = refresh TTL remaining

  jti_denylist:{jti}                  SET "1" EX access_ttl

Reuse detection: refresh tokens rotate. Each rotation deletes the previous
`session:{old_hash}` and writes `session:{new_hash}`. If a *deleted* hash is
later presented, the whole family is killed — R6.03 "Reuse of a consumed
refresh invalidates the whole session family".

DB mirror (`sessions` row) is maintained by `contexts.identity` so the
"list my active sessions" UI (R6.08) can render without scanning Redis.
That mirror is not authoritative; Redis is.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from app.config.settings import get_settings
from shared_kernel.auth.clients import get_redis, now

_SESSION_PREFIX: Final = "session:"
_FAMILY_PREFIX: Final = "session_family:"
_DENYLIST_PREFIX: Final = "jti_denylist:"
_REFRESH_BYTES: Final = 48


@dataclass(frozen=True, slots=True)
class RefreshRecord:
    session_id: uuid.UUID
    user_id: uuid.UUID
    family_id: uuid.UUID
    last_jti: uuid.UUID | None
    issued_at: datetime
    expires_at: datetime


def hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(_REFRESH_BYTES)


async def create_session(
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    family_id: uuid.UUID | None = None,
    last_jti: uuid.UUID | None = None,
    ttl: timedelta | None = None,
) -> tuple[str, RefreshRecord]:
    """Issue a fresh refresh token and persist the session record."""
    cfg = get_settings().jwt
    family = family_id or uuid.uuid4()
    expires = now() + (ttl or timedelta(seconds=cfg.refresh_ttl_seconds))
    token = new_refresh_token()
    record = RefreshRecord(
        session_id=session_id,
        user_id=user_id,
        family_id=family,
        last_jti=last_jti,
        issued_at=now(),
        expires_at=expires,
    )
    await _write_session(token, record)
    return token, record


async def rotate_session(
    old_token: str, *, new_jti: uuid.UUID
) -> tuple[str, RefreshRecord, str]:
    """Consume `old_token`, mint a new one.

    Returns `(new_refresh_token, updated_record, old_hash)`. The caller uses
    `old_hash` to update the DB mirror row. Raises `TokenReuseError` if the
    old hash is missing or expired → whole family is killed as a side-effect.
    """
    r = get_redis()
    old_hash = hash_refresh(old_token)
    raw: dict[str, str] = await r.hgetall(_SESSION_PREFIX + old_hash)
    if not raw:
        await _kill_family_by_hash(old_hash)
        raise TokenReuseError("refresh token not recognised — session family killed")

    record = _parse_record(raw)
    if record.expires_at < now():
        await r.delete(_SESSION_PREFIX + old_hash)
        raise TokenReuseError("refresh token expired")

    new_token = new_refresh_token()
    new_hash = hash_refresh(new_token)
    new_record = RefreshRecord(
        session_id=record.session_id,
        user_id=record.user_id,
        family_id=record.family_id,
        last_jti=new_jti,
        issued_at=record.issued_at,
        expires_at=record.expires_at,
    )
    ttl_seconds = max(1, int((record.expires_at - now()).total_seconds()))
    async with r.pipeline(transaction=True) as pipe:
        pipe.delete(_SESSION_PREFIX + old_hash)
        pipe.hset(_SESSION_PREFIX + new_hash, mapping=_dump_record(new_record))
        pipe.expire(_SESSION_PREFIX + new_hash, ttl_seconds)
        pipe.srem(_FAMILY_PREFIX + str(record.family_id), old_hash)
        pipe.sadd(_FAMILY_PREFIX + str(record.family_id), new_hash)
        pipe.expire(_FAMILY_PREFIX + str(record.family_id), ttl_seconds)
        await pipe.execute()
    return new_token, new_record, old_hash


async def revoke_session(refresh_token: str) -> RefreshRecord | None:
    """Delete the session for `refresh_token` (logout / `DELETE /sessions/{id}`)."""
    r = get_redis()
    h = hash_refresh(refresh_token)
    raw: dict[str, str] = await r.hgetall(_SESSION_PREFIX + h)
    if not raw:
        return None
    record = _parse_record(raw)
    async with r.pipeline(transaction=True) as pipe:
        pipe.delete(_SESSION_PREFIX + h)
        pipe.srem(_FAMILY_PREFIX + str(record.family_id), h)
        await pipe.execute()
    return record


async def kill_family(family_id: uuid.UUID) -> int:
    """Revoke every session in the family — used by password change (R6.06)."""
    r = get_redis()
    fkey = _FAMILY_PREFIX + str(family_id)
    hashes = await r.smembers(fkey)
    if not hashes:
        return 0
    async with r.pipeline(transaction=True) as pipe:
        for h in hashes:
            pipe.delete(_SESSION_PREFIX + h)
        pipe.delete(fkey)
        await pipe.execute()
    return len(hashes)


async def get_record(refresh_token: str) -> RefreshRecord | None:
    raw: dict[str, str] = await get_redis().hgetall(_SESSION_PREFIX + hash_refresh(refresh_token))
    return _parse_record(raw) if raw else None


# ---- jti denylist ---------------------------------------------------------


async def deny_jti(jti: uuid.UUID, *, ttl: timedelta) -> None:
    """Short-circuit any future access-token check for this jti."""
    seconds = max(1, int(ttl.total_seconds()))
    await get_redis().set(_DENYLIST_PREFIX + str(jti), "1", ex=seconds)


async def is_denied(jti: uuid.UUID) -> bool:
    return bool(await get_redis().exists(_DENYLIST_PREFIX + str(jti)))


# ---- internals ------------------------------------------------------------


class TokenReuseError(RuntimeError):
    """Raised when a refresh token is reused or missing — family is killed."""


async def _write_session(refresh_token: str, record: RefreshRecord) -> None:
    r = get_redis()
    h = hash_refresh(refresh_token)
    ttl_seconds = max(1, int((record.expires_at - now()).total_seconds()))
    async with r.pipeline(transaction=True) as pipe:
        pipe.hset(_SESSION_PREFIX + h, mapping=_dump_record(record))
        pipe.expire(_SESSION_PREFIX + h, ttl_seconds)
        pipe.sadd(_FAMILY_PREFIX + str(record.family_id), h)
        pipe.expire(_FAMILY_PREFIX + str(record.family_id), ttl_seconds)
        await pipe.execute()


async def _kill_family_by_hash(refresh_hash: str) -> None:
    """Best-effort: if a stale hash maps to a known family, kill it."""
    # We don't index hash→family, so this is an O(N) scan across family sets.
    # Families are short-lived (≤ 30 days); miss rate is low, O(small).
    r = get_redis()
    cursor = 0
    while True:
        cursor, keys = await r.scan(cursor=cursor, match=_FAMILY_PREFIX + "*", count=100)
        for fkey in keys:
            if await r.sismember(fkey, refresh_hash):
                family = fkey.removeprefix(_FAMILY_PREFIX)
                await kill_family(uuid.UUID(family))
        if cursor == 0:
            break


def _dump_record(r: RefreshRecord) -> dict[str, str]:
    return {
        "session_id": str(r.session_id),
        "user_id": str(r.user_id),
        "family_id": str(r.family_id),
        "last_jti": "" if r.last_jti is None else str(r.last_jti),
        "issued_at": r.issued_at.isoformat(),
        "expires_at": r.expires_at.isoformat(),
    }


def _parse_record(raw: dict[str, Any]) -> RefreshRecord:
    return RefreshRecord(
        session_id=uuid.UUID(raw["session_id"]),
        user_id=uuid.UUID(raw["user_id"]),
        family_id=uuid.UUID(raw["family_id"]),
        last_jti=uuid.UUID(raw["last_jti"]) if raw.get("last_jti") else None,
        issued_at=datetime.fromisoformat(raw["issued_at"]),
        expires_at=datetime.fromisoformat(raw["expires_at"]),
    )


__all__ = [
    "RefreshRecord",
    "TokenReuseError",
    "create_session",
    "deny_jti",
    "get_record",
    "hash_refresh",
    "is_denied",
    "kill_family",
    "new_refresh_token",
    "revoke_session",
    "rotate_session",
]
