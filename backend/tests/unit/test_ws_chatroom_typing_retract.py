"""F-18 regression — a closing connection retracts its own typing indicator.

``presence.leave`` reports ``left=False`` while a sibling connection of the same
user remains in the room, and the ``if left:`` guard in ``on_close`` suppressed
every publish, so a user who was typing in a tab that died kept "U is typing"
pinned for every other member. Nothing expires typing state: there is no
server-side TTL and the client clears only on ``typing.stop`` /
``presence.left``.

The defect was invisible in production only because the socket churn of F-1
supplied a reconnect (and with it a ``resyncPresence`` typing clear) every two
minutes. Fixing F-1 without this would have made it permanent, which is why the
two land together — see docs/tasks/2026-07-22-chatroom-socket-lifecycle/spec.md.

Harness follows test_ws_auth_watchdog.py: ``connection_loop`` is replaced with a
stub that captures the callbacks the route registers, which are then driven
directly.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import pytest

from app.api.ws import chatroom as chatroom_mod
from shared_kernel.auth.permissions import Principal


class _RecordingPublisher:
    """Captures every event the route publishes to the room channel."""

    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    emitted: ClassVar[list[tuple[str, dict[str, Any]]]] = []

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        _RecordingPublisher.emitted.append((event_type, payload))


class _SiblingConnectionPresence:
    """PresenceTracker whose `leave` reports that another connection of the
    same user is still in the room (presence.py:139-141) — the case that
    suppresses `presence.left` and, before the fix, every other publish."""

    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    async def join(self, **_k: object) -> tuple[bool, int]:
        return (False, 1)

    async def leave(self, **_k: object) -> tuple[bool, int]:
        return (False, -1)

    async def heartbeat(self, **_k: object) -> None:
        return None


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def begin(self) -> _FakeSession:
        return self


class _FakeWS:
    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None


class _FakeAuth:
    def __init__(self) -> None:
        self.principal = Principal(
            user_id=uuid.uuid4(),
            is_admin=False,
            email_verified=True,
        )
        self.subprotocol = ""
        self.expires_at = None
        self.jti = None


class _FakeConnection:
    def __init__(self, principal: Principal) -> None:
        self.principal = principal
        self.connection_id = uuid.uuid4()


@pytest.fixture
def callbacks(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Drive the route far enough to capture the callbacks it registers."""
    _RecordingPublisher.emitted = []
    captured: dict[str, Any] = {}

    auth = _FakeAuth()

    async def _fake_authenticate(_ws: object) -> _FakeAuth:
        return auth

    async def _fake_resolve(*_a: object, **_k: object) -> object:
        return object()

    async def _fake_connection_loop(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(chatroom_mod, "connection_loop", _fake_connection_loop)
    monkeypatch.setattr(chatroom_mod, "authenticate_subprotocol", _fake_authenticate)
    monkeypatch.setattr(chatroom_mod, "resolve_room_access", _fake_resolve)
    monkeypatch.setattr(chatroom_mod, "ensure_can_read", lambda *_a, **_k: None)
    monkeypatch.setattr(chatroom_mod, "get_sessionmaker", lambda: _FakeSession)
    monkeypatch.setattr(chatroom_mod, "PresenceTracker", _SiblingConnectionPresence)
    monkeypatch.setattr(chatroom_mod, "Publisher", _RecordingPublisher)

    captured["_auth"] = auth
    return captured


async def _drive(callbacks: dict[str, Any]) -> _FakeConnection:
    await chatroom_mod.ws_chatroom(_FakeWS(), uuid.uuid4())  # type: ignore[arg-type]
    return _FakeConnection(callbacks["_auth"].principal)


def _emitted_types() -> list[str]:
    return [event_type for event_type, _ in _RecordingPublisher.emitted]


async def test_close_retracts_typing_when_sibling_connection_remains(
    callbacks: dict[str, Any],
) -> None:
    """The F-18 lock: the retraction must not sit behind the `left` guard."""
    conn = await _drive(callbacks)

    await callbacks["on_client_message"](conn, {"type": "typing.start"})
    await callbacks["on_close"](conn)

    assert _emitted_types() == ["typing.start", "typing.stop"]
    # A sibling connection remains, so the user has not left the room.
    assert "presence.left" not in _emitted_types()


async def test_close_without_typing_publishes_nothing(
    callbacks: dict[str, Any],
) -> None:
    """Pins the `_typing_active` gate (Q-4). An unconditional retraction would
    blank an indicator a sibling tab legitimately asserted."""
    conn = await _drive(callbacks)

    await callbacks["on_close"](conn)

    assert _emitted_types() == []


async def test_explicit_typing_stop_clears_the_retraction_flag(
    callbacks: dict[str, Any],
) -> None:
    """Fails a naive fix that sets the flag on start but never clears it,
    producing a duplicate `typing.stop` on every close."""
    conn = await _drive(callbacks)

    await callbacks["on_client_message"](conn, {"type": "typing.start"})
    await callbacks["on_client_message"](conn, {"type": "typing.stop"})
    await callbacks["on_close"](conn)

    assert _emitted_types() == ["typing.start", "typing.stop"]
