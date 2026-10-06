"""Socket refusals carry their code, and a dead room closes 4404 -- AC-3, AC-4 (F-16).

Spec: ``docs/tasks/2026-10-05-guest-session-backend-hardening/spec.md`` §7.3, Q-3, Q-4.

Before the fix the chat and canvas handshakes caught ``ChatroomNotFound`` and
``ForbiddenInRoom`` only, closed before ``accept`` (which the browser sees as
1006, losing the code), and let ``WorkspaceNotFound`` escape. Their watchdog
probes had the same narrow catch, and ``connection_loop`` swallowed the escaping
error and retried forever, so a socket outlived its workspace indefinitely.

Harness follows ``test_ws_chatroom_typing_retract.py``: the route's collaborators
are replaced on the module and ``connection_loop`` is captured, not run.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from app.api.ws import canvas as canvas_mod
from app.api.ws import chatroom as chatroom_mod
from contexts.conversation.domain.errors import ChatroomNotFound, ForbiddenInRoom, GuestRemoved, WorkspaceNotFound
from shared_kernel.auth import tokens as tokens_mod
from shared_kernel.auth.clients import now
from shared_kernel.auth.permissions import Principal
from shared_kernel.realtime import connection as conn_mod

_SUBPROTOCOL = "ticket.abc"

# The access errors a dead or closed room raises, and the code each must reach
# the browser with. A deleted project raises ChatroomNotFound from the resolver.
_REFUSALS = [
    pytest.param(WorkspaceNotFound, 4404, id="workspace-deleted"),
    pytest.param(ChatroomNotFound, 4404, id="room-or-project-deleted"),
    pytest.param(ForbiddenInRoom, 4403, id="forbidden"),
    # [R13.07a]: a subclass of ForbiddenInRoom, so it must be told apart first.
    pytest.param(GuestRemoved, 4408, id="guest-removed"),
]


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def begin(self) -> _FakeSession:
        return self


class _RecordingWS:
    def __init__(self) -> None:
        self.accepted_with: str | None = None
        self.closed: int | None = None

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted_with = subprotocol

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = code


def _auth() -> SimpleNamespace:
    return SimpleNamespace(
        principal=Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True),
        subprotocol=_SUBPROTOCOL,
        expires_at=None,
        jti=None,
    )


def _raising(exc: type[Exception]) -> Any:
    async def resolve(*_a: object, **_k: object) -> object:
        raise exc("gone")

    return resolve


async def _allowed(*_a: object, **_k: object) -> object:
    return object()


def _patch_route(monkeypatch: pytest.MonkeyPatch, mod: Any, resolve: Any, captured: dict[str, Any]) -> None:
    auth = _auth()

    async def authenticate(_ws: object) -> SimpleNamespace:
        return auth

    async def loop(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(mod, "authenticate_subprotocol", authenticate)
    monkeypatch.setattr(mod, "resolve_room_access", resolve)
    monkeypatch.setattr(mod, "ensure_can_read", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "get_sessionmaker", lambda: _FakeSession)
    monkeypatch.setattr(mod, "connection_loop", loop)
    captured["_principal"] = auth.principal


class _Canvases:
    def __init__(self, _session: object) -> None:
        pass

    async def get(self, canvas_id: uuid.UUID) -> object:
        return SimpleNamespace(id=canvas_id, chatroom_id=uuid.uuid4(), crdt_state=b"state")

    async def list_objects(self, _canvas_id: uuid.UUID) -> list[object]:
        return []


class _NoCanvases(_Canvases):
    async def get(self, canvas_id: uuid.UUID) -> object:
        return None


def _patch_canvas(monkeypatch: pytest.MonkeyPatch, resolve: Any, captured: dict[str, Any]) -> None:
    _patch_route(monkeypatch, canvas_mod, resolve, captured)
    monkeypatch.setattr(canvas_mod, "CanvasRepository", _Canvases)

    async def editors(*_a: object) -> int:
        return 1

    monkeypatch.setattr(canvas_mod, "_register_editor", editors)
    monkeypatch.setattr(canvas_mod, "_unregister_editor", editors)


# -- handshake --


@pytest.mark.parametrize(("raised", "code"), _REFUSALS)
async def test_chat_handshake_refusal_is_accepted_then_closed_with_its_code(
    monkeypatch: pytest.MonkeyPatch, raised: type[Exception], code: int
) -> None:
    captured: dict[str, Any] = {}
    _patch_route(monkeypatch, chatroom_mod, _raising(raised), captured)
    ws = _RecordingWS()

    await chatroom_mod.ws_chatroom(ws, uuid.uuid4())  # type: ignore[arg-type]

    assert (ws.accepted_with, ws.closed) == (_SUBPROTOCOL, code)
    assert "authorize" not in captured


@pytest.mark.parametrize(("raised", "code"), _REFUSALS)
async def test_canvas_handshake_refusal_is_accepted_then_closed_with_its_code(
    monkeypatch: pytest.MonkeyPatch, raised: type[Exception], code: int
) -> None:
    captured: dict[str, Any] = {}
    _patch_canvas(monkeypatch, _raising(raised), captured)
    ws = _RecordingWS()

    await canvas_mod.ws_canvas(ws, uuid.uuid4())  # type: ignore[arg-type]

    assert (ws.accepted_with, ws.closed) == (_SUBPROTOCOL, code)
    assert "authorize" not in captured


class _LegacyCanvases(_Canvases):
    loaded: ClassVar[int] = 0

    async def get(self, canvas_id: uuid.UUID) -> object:
        return SimpleNamespace(id=canvas_id, chatroom_id=uuid.uuid4(), crdt_state=None)

    async def list_objects(self, _canvas_id: uuid.UUID) -> list[object]:
        _LegacyCanvases.loaded += 1
        return []


async def test_a_refused_caller_never_triggers_the_legacy_object_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _patch_canvas(monkeypatch, _raising(ForbiddenInRoom), captured)
    monkeypatch.setattr(canvas_mod, "CanvasRepository", _LegacyCanvases)
    _LegacyCanvases.loaded = 0

    await canvas_mod.ws_canvas(_RecordingWS(), uuid.uuid4())  # type: ignore[arg-type]

    assert _LegacyCanvases.loaded == 0


async def test_a_missing_canvas_reads_like_a_forbidden_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Security audit H-1: 4404 here would let any signed-in caller probe canvas ids."""
    captured: dict[str, Any] = {}
    _patch_canvas(monkeypatch, _allowed, captured)
    monkeypatch.setattr(canvas_mod, "CanvasRepository", _NoCanvases)
    ws = _RecordingWS()

    await canvas_mod.ws_canvas(ws, uuid.uuid4())  # type: ignore[arg-type]

    assert (ws.accepted_with, ws.closed) == (_SUBPROTOCOL, 4403)


# -- watchdog probe --


async def _authorize(monkeypatch: pytest.MonkeyPatch, mod: Any, resolve: Any) -> Any:
    """Open the route allowed, then return its watchdog probe's verdict under ``resolve``."""
    captured: dict[str, Any] = {}
    if mod is canvas_mod:
        _patch_canvas(monkeypatch, _allowed, captured)
        await canvas_mod.ws_canvas(_RecordingWS(), uuid.uuid4())  # type: ignore[arg-type]
    else:
        _patch_route(monkeypatch, chatroom_mod, _allowed, captured)
        await chatroom_mod.ws_chatroom(_RecordingWS(), uuid.uuid4())  # type: ignore[arg-type]
    monkeypatch.setattr(mod, "resolve_room_access", resolve)
    return await captured["authorize"](SimpleNamespace(principal=captured["_principal"]))


@pytest.mark.parametrize("mod", [chatroom_mod, canvas_mod], ids=["chat", "canvas"])
@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        pytest.param(WorkspaceNotFound, "GONE", id="workspace-deleted"),
        pytest.param(ChatroomNotFound, "GONE", id="room-or-project-deleted"),
        pytest.param(ForbiddenInRoom, "FORBIDDEN", id="forbidden"),
        pytest.param(GuestRemoved, "REMOVED", id="guest-removed"),
        pytest.param(None, "ALLOWED", id="allowed"),
    ],
)
async def test_watchdog_probe_reports_the_outcome(
    monkeypatch: pytest.MonkeyPatch, mod: Any, raised: type[Exception] | None, expected: str
) -> None:
    from shared_kernel.realtime import AccessOutcome

    outcome = await _authorize(monkeypatch, mod, _allowed if raised is None else _raising(raised))
    assert outcome is AccessOutcome[expected]


# -- connection_loop closes on the probe's outcome --


class _FakeRedis:
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

    async def exists(self, _key: str) -> int:
        return 0


class _LiveWS:
    def __init__(self) -> None:
        self.closed: int | None = None
        self._gate = asyncio.Event()

    async def accept(self, subprotocol: str | None = None) -> None:
        return None

    async def receive_text(self) -> str:
        await self._gate.wait()
        return "{}"

    async def send_text(self, _data: str) -> None:
        return None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = code


@pytest.mark.parametrize(("name", "code"), [("GONE", 4404), ("FORBIDDEN", 4403), ("REMOVED", 4408)])
async def test_connection_loop_closes_with_the_probes_code(
    monkeypatch: pytest.MonkeyPatch, name: str, code: int
) -> None:
    from shared_kernel.realtime import AccessOutcome

    fake = _FakeRedis()
    monkeypatch.setattr(conn_mod, "get_redis", lambda: fake)
    monkeypatch.setattr(tokens_mod, "get_redis", lambda: fake)
    monkeypatch.setattr(conn_mod, "_AUTH_RECHECK_SECONDS", 0.01)

    async def authorize(_conn: object) -> AccessOutcome:
        return AccessOutcome[name]

    ws = _LiveWS()
    await asyncio.wait_for(
        conn_mod.connection_loop(
            ws=ws,  # type: ignore[arg-type]
            principal=Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True),
            subprotocol="",
            channels=[],
            token_expires_at=now() + timedelta(hours=1),
            token_jti=uuid.uuid4(),
            authorize=authorize,
        ),
        timeout=5.0,
    )
    assert ws.closed == code
