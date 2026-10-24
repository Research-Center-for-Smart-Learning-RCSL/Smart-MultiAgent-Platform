"""AC-1: any first connection to a room is an arrival, not only the first into an empty room.

Harness follows test_ws_chatroom_typing_retract.py: ``connection_loop`` is
replaced with a stub that captures the route's callbacks, which are driven directly.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import pytest

from app.api.ws import chatroom as chatroom_mod
from shared_kernel.auth.permissions import Principal


class _Publisher:
    emitted: ClassVar[list[str]] = []

    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        _Publisher.emitted.append(event_type)


class _Presence:
    join_result: ClassVar[tuple[bool, int]] = (True, 1)

    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    async def join(self, **_k: object) -> tuple[bool, int]:
        return _Presence.join_result


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def begin(self) -> _Session:
        return self


class _WS:
    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None


class _Auth:
    def __init__(self) -> None:
        self.principal = Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True)
        self.subprotocol = ""
        self.expires_at = None
        self.jti = None


class _Conn:
    def __init__(self, principal: Principal) -> None:
        self.principal = principal
        self.connection_id = uuid.uuid4()


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    _Publisher.emitted = []
    captured: dict[str, Any] = {"notified": []}
    auth = _Auth()

    async def _authenticate(_ws: object) -> _Auth:
        return auth

    async def _resolve(*_a: object, **_k: object) -> object:
        return object()

    async def _loop(**kwargs: Any) -> None:
        captured.update(kwargs)

    async def _notify(chatroom_id: uuid.UUID, principal_id: uuid.UUID) -> None:
        captured["notified"].append((chatroom_id, principal_id))

    monkeypatch.setattr(chatroom_mod, "connection_loop", _loop)
    monkeypatch.setattr(chatroom_mod, "authenticate_subprotocol", _authenticate)
    monkeypatch.setattr(chatroom_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(chatroom_mod, "ensure_can_read", lambda *_a, **_k: None)
    monkeypatch.setattr(chatroom_mod, "get_sessionmaker", lambda: _Session)
    monkeypatch.setattr(chatroom_mod, "PresenceTracker", _Presence)
    monkeypatch.setattr(chatroom_mod, "Publisher", _Publisher)
    monkeypatch.setattr(chatroom_mod, "_notify_presence", _notify)
    captured["_auth"] = auth
    return captured


async def _open(harness: dict[str, Any]) -> tuple[uuid.UUID, Principal]:
    room = uuid.uuid4()
    await chatroom_mod.ws_chatroom(_WS(), room)  # type: ignore[arg-type]
    principal = harness["_auth"].principal
    await harness["on_open"](_Conn(principal))
    return room, principal


@pytest.mark.parametrize("roster_size", [1, 2, 7])
async def test_first_connection_notifies_whatever_the_roster_size(
    harness: dict[str, Any], roster_size: int
) -> None:
    _Presence.join_result = (True, roster_size)

    room, principal = await _open(harness)

    assert harness["notified"] == [(room, principal.user_id)]
    assert _Publisher.emitted == ["presence.joined"]


async def test_second_tab_of_a_connected_principal_is_not_an_arrival(harness: dict[str, Any]) -> None:
    _Presence.join_result = (False, 1)

    await _open(harness)

    assert harness["notified"] == []
    assert _Publisher.emitted == []
