"""SEC-H2 regression — a live WebSocket is torn down once its token expires or
its jti is denylisted, not only when the client voluntarily refreshes.

Before the fix the handshake authorized the token once and the socket then
streamed events indefinitely on a 15-minute access token even after logout /
ban / session-kill. ``connection_loop`` now runs an auth watchdog that
re-checks expiry (locally) and the denylist (Redis) on a fixed cadence and
closes with code 4401.

The watchdog's recheck interval is monkeypatched down so the test runs fast;
each ``connection_loop`` call is wrapped in ``wait_for`` so a regression that
fails to tear down surfaces as a timeout instead of a hang.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest

from shared_kernel.auth import tokens as tokens_mod
from shared_kernel.auth.clients import now
from shared_kernel.auth.permissions import Principal
from shared_kernel.realtime import connection as conn_mod

_CLOSE_AUTH_FAILED = 4401


class _FakeRedis:
    """Covers the per-user cap ZSET ops + the denylist EXISTS probe."""

    def __init__(self, *, denied: bool = False) -> None:
        self._denied = denied

    async def zremrangebyscore(self, *_a: object, **_k: object) -> int:
        return 0

    async def zadd(self, *_a: object, **_k: object) -> int:
        return 1

    async def zcard(self, *_a: object, **_k: object) -> int:
        return 1

    async def expire(self, *_a: object, **_k: object) -> bool:
        return True

    async def zrem(self, *_a: object, **_k: object) -> int:
        return 1

    async def delete(self, *_a: object, **_k: object) -> int:
        return 1

    async def exists(self, _key: str) -> int:
        return 1 if self._denied else 0


class _FakeWS:
    def __init__(self) -> None:
        self.accepted = False
        self.closed: tuple[int, str] | None = None
        self._gate = asyncio.Event()  # never set → receive_text blocks until cancel

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        await self._gate.wait()
        return "{}"

    async def send_text(self, _data: str) -> None:
        return None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


def _principal() -> Principal:
    return Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True)


async def _drive(ws: _FakeWS, *, expires_at: object, jti: uuid.UUID) -> None:
    await asyncio.wait_for(
        conn_mod.connection_loop(
            ws=ws,
            principal=_principal(),
            subprotocol="",
            channels=[],
            token_expires_at=expires_at,  # type: ignore[arg-type]
            token_jti=jti,
        ),
        timeout=5.0,
    )


def _patch_redis(monkeypatch: pytest.MonkeyPatch, fake: _FakeRedis) -> None:
    monkeypatch.setattr(conn_mod, "get_redis", lambda: fake)
    monkeypatch.setattr(tokens_mod, "get_redis", lambda: fake)
    monkeypatch.setattr(conn_mod, "_AUTH_RECHECK_SECONDS", 0.01)


async def test_watchdog_closes_on_token_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, _FakeRedis(denied=False))
    ws = _FakeWS()
    await _drive(ws, expires_at=now() - timedelta(seconds=1), jti=uuid.uuid4())
    assert ws.closed == (_CLOSE_AUTH_FAILED, "token expired")


async def test_watchdog_closes_on_denylist(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, _FakeRedis(denied=True))
    ws = _FakeWS()
    # Not expired — the denylist hit is what must tear it down.
    await _drive(ws, expires_at=now() + timedelta(hours=1), jti=uuid.uuid4())
    assert ws.closed == (_CLOSE_AUTH_FAILED, "token revoked")
