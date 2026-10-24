"""AC-1: any first connection to a room is an arrival, not only the first into an empty room;
AC-5: the arrival wakes it owes are enqueued deferred.

Harness follows test_ws_chatroom_typing_retract.py: ``connection_loop`` is
replaced with a stub that captures the route's callbacks, which are driven directly.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from app.api.ws import chatroom as chatroom_mod
from contexts.orchestration.interfaces.facade import ON_JOIN_COALESCE_SECONDS
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


async def test_each_owed_arrival_wake_is_deferred_to_the_end_of_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-5: one ``on_join`` job per target, deferred by the coalescing window."""
    room, who = uuid.uuid4(), uuid.uuid4()
    target = SimpleNamespace(agent_id=uuid.uuid4(), binding_id=uuid.uuid4())
    jobs: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def _evaluate(_db: object, *, chatroom_id: uuid.UUID, principal_id: uuid.UUID) -> list[Any]:
        assert (chatroom_id, principal_id) == (room, who)
        return [target]

    async def _enqueue(*args: Any, **kwargs: Any) -> None:
        jobs.append((args, kwargs))

    class _Db(_Session):
        async def commit(self) -> None:
            return None

    monkeypatch.setattr(chatroom_mod, "async_session", _Db)
    monkeypatch.setattr(chatroom_mod, "evaluate_presence_change", _evaluate)
    monkeypatch.setattr(chatroom_mod, "enqueue", _enqueue)

    await chatroom_mod._notify_presence(room, who)

    assert jobs == [
        (
            ("wakeup_agent", str(target.agent_id), str(room), "on_join", None, str(target.binding_id)),
            {"_defer_by": timedelta(seconds=ON_JOIN_COALESCE_SECONDS)},
        )
    ]


async def test_one_failed_enqueue_does_not_cost_the_other_targets_their_wake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each target already holds its batch claim, so a skipped enqueue is a lost wake."""
    first, second = (SimpleNamespace(agent_id=uuid.uuid4(), binding_id=uuid.uuid4()) for _ in range(2))
    enqueued: list[str] = []

    async def _evaluate(_db: object, **_k: object) -> list[Any]:
        return [first, second]

    async def _enqueue(_name: str, agent_id: str, *_a: Any, **_k: Any) -> None:
        if agent_id == str(first.agent_id):
            raise ConnectionError("queue down")
        enqueued.append(agent_id)

    class _Db(_Session):
        async def commit(self) -> None:
            return None

    monkeypatch.setattr(chatroom_mod, "async_session", _Db)
    monkeypatch.setattr(chatroom_mod, "evaluate_presence_change", _evaluate)
    monkeypatch.setattr(chatroom_mod, "enqueue", _enqueue)

    await chatroom_mod._notify_presence(uuid.uuid4(), uuid.uuid4())

    assert enqueued == [str(second.agent_id)]
