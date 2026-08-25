"""The `draft.update` / `draft.clear` frames on the room socket (§32, [R32.01]).

Four properties, and the first is the one this whole file exists for.

**A draft frame publishes nothing and wakes nothing.** [R32.01] says a draft frame
"publishes no room event, wakes no agent, does not re-arm the silence clock, and is
not counted by ``every_n_messages``" — the point of the feature being that the only
path from unsent text to a model is a tool the model chose to call. Nothing in the
handler's shape makes that automatic; it is true because the handler contains no
`publisher.emit` and no trigger call, and the obvious "improvement" for a later
reader is to add one so the room can show a richer typing indicator. That is what
`TestNothingLeavesTheServer` is a tripwire for.

**A room nobody may read stores nothing** (AC-1), resolved once per connection with
a bounded re-resolve rather than per frame.

**The throttle** (AC-3), which must be the draft path's own rather than shared with
`typing.start`.

**Malformed frames are dropped in silence**, never answered — an error reply would be
a probe for the room's grant state.

Harness follows `test_ws_chatroom_typing_retract.py`: `connection_loop` is replaced
with a stub that captures the callbacks the route registers, which are then driven
directly.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import pytest

from app.api.ws import chatroom as chatroom_mod
from shared_kernel.auth.permissions import Principal


class _RecordingPublisher:
    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    emitted: ClassVar[list[tuple[str, dict[str, Any]]]] = []

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        _RecordingPublisher.emitted.append((event_type, payload))


class _Presence:
    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    async def join(self, **_k: object) -> tuple[bool, int]:
        return (False, 2)

    async def leave(self, **_k: object) -> tuple[bool, int]:
        return (False, -1)

    async def heartbeat(self, **_k: object) -> None:
        return None

    async def typing_start(self, **_k: object) -> bool:
        return True

    async def typing_stop(self, **_k: object) -> bool:
        return True

    async def typing_heartbeat(self, **_k: object) -> None:
        return None


class _RecordingDraftStore:
    """Captures every store call the route makes, without touching Redis."""

    puts: ClassVar[list[dict[str, Any]]] = []
    clears: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    async def put(self, **kw: Any) -> bool:
        _RecordingDraftStore.puts.append(kw)
        return True

    async def clear(self, **kw: Any) -> None:
        _RecordingDraftStore.clears.append(kw)


class _Facade:
    """Stands in for `ConversationFacade`, counting the grant reads."""

    grant: ClassVar[bool] = True
    reads: ClassVar[int] = 0
    raises: ClassVar[bool] = False

    def __init__(self, _db: object) -> None:
        pass

    async def room_has_draft_reader(self, _chatroom_id: uuid.UUID) -> bool:
        _Facade.reads += 1
        if _Facade.raises:
            raise RuntimeError("database is down")
        return _Facade.grant


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
        self.principal = Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True)
        self.subprotocol = ""
        self.expires_at = None
        self.jti = None


class _FakeConnection:
    def __init__(self, principal: Principal) -> None:
        self.principal = principal
        self.connection_id = uuid.uuid4()


@pytest.fixture
def callbacks(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    _RecordingPublisher.emitted = []
    _RecordingDraftStore.puts = []
    _RecordingDraftStore.clears = []
    _Facade.grant = True
    _Facade.reads = 0
    _Facade.raises = False
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
    monkeypatch.setattr(chatroom_mod, "async_session", _FakeSession)
    monkeypatch.setattr(chatroom_mod, "PresenceTracker", _Presence)
    monkeypatch.setattr(chatroom_mod, "Publisher", _RecordingPublisher)
    monkeypatch.setattr(chatroom_mod, "DraftStore", _RecordingDraftStore)
    monkeypatch.setattr(chatroom_mod, "ConversationFacade", _Facade)
    # The throttle and the grant window are both real time; freezing the clock by
    # default would make every test here depend on wall-clock ordering. Each
    # monotonic call advances past BOTH windows (2s throttle, 60s grant), so by
    # default every frame is un-throttled and re-resolves its grant. A test that
    # needs two frames inside one window opts back in via `stopped_clock`.
    ticks = iter(range(0, 10_000_000, 100))
    monkeypatch.setattr(chatroom_mod.time, "monotonic", lambda: float(next(ticks)))

    captured["_auth"] = auth
    return captured


@pytest.fixture
def stopped_clock(monkeypatch: pytest.MonkeyPatch, callbacks: dict[str, Any]) -> None:
    """Freeze `time.monotonic`, so consecutive frames land inside one window."""
    monkeypatch.setattr(chatroom_mod.time, "monotonic", lambda: 1000.0)


async def _drive(callbacks: dict[str, Any]) -> _FakeConnection:
    await chatroom_mod.ws_chatroom(_FakeWS(), uuid.uuid4())  # type: ignore[arg-type]
    return _FakeConnection(callbacks["_auth"].principal)


class TestNothingLeavesTheServer:
    """AC-9 / [R32.01]. The regression a later "improvement" is most likely to add."""

    async def test_a_draft_update_publishes_no_room_event(self, callbacks: dict[str, Any]) -> None:
        """A draft frame must produce nothing on the room channel. Publishing one
        would hand every member the fact that a named participant is composing —
        which is what `typing.start` already does, carrying no content — or, worse,
        the content itself, which is Option A of §5 and was rejected precisely
        because `typing.*` reaches the whole room."""
        conn = await _drive(callbacks)

        await callbacks["on_client_message"](
            conn, {"type": "draft.update", "surface": "composer", "content": "half a thought"}
        )

        assert _RecordingPublisher.emitted == []
        assert len(_RecordingDraftStore.puts) == 1

    async def test_a_draft_clear_publishes_no_room_event(self, callbacks: dict[str, Any]) -> None:
        conn = await _drive(callbacks)

        await callbacks["on_client_message"](conn, {"type": "draft.clear", "surface": "composer"})

        assert _RecordingPublisher.emitted == []

    async def test_a_draft_frame_does_not_arm_the_typing_retraction(self, callbacks: dict[str, Any]) -> None:
        """`_typing_active` decides whether `on_close` publishes `typing.stop`. A
        draft frame that armed it would make closing a tab announce a typing stop the
        user never started — and it is the kind of coupling that appears by accident
        when two frames are handled in one function."""
        conn = await _drive(callbacks)

        await callbacks["on_client_message"](
            conn, {"type": "draft.update", "surface": "composer", "content": "x"}
        )
        await callbacks["on_close"](conn)

        assert _RecordingPublisher.emitted == []

    def test_the_handler_contains_no_publish_and_no_trigger(self) -> None:
        """A structural check beside the behavioural ones above.

        The behavioural tests drive `on_client_message`, so they can only see what
        the *current* branches do. This reads the source of the draft handler itself,
        so a publish or a wake-up evaluation added to it fails even if some new
        branch routes around the drives above. [R32.01] is a claim about the code
        path, and this is the closest a unit test gets to asserting one.
        """
        import inspect

        source = inspect.getsource(chatroom_mod.ws_chatroom)
        handler = source.split("async def _handle_draft")[1].split("async def on_client_message")[0]
        # Everything after the docstring's closing quotes. The docstring *names*
        # what the handler must not do, so scanning it would make this test assert
        # that the explanation is absent rather than the behaviour.
        body = handler.split('"""')[2]

        for forbidden in ("publisher.emit", "evaluate_presence_change", "_notify_presence", "typing"):
            assert forbidden not in body, f"the draft handler mentions {forbidden!r}"


class TestARoomNobodyMayReadStoresNothing:
    """AC-1."""

    async def test_no_grant_means_no_write(self, callbacks: dict[str, Any]) -> None:
        _Facade.grant = False
        conn = await _drive(callbacks)

        await callbacks["on_client_message"](
            conn, {"type": "draft.update", "surface": "composer", "content": "unsent"}
        )

        assert _RecordingDraftStore.puts == []

    async def test_a_grant_read_that_raises_stores_nothing(self, callbacks: dict[str, Any]) -> None:
        """Fail closed: a database fault must cost the feature, never the
        disclosure. The alternative — keeping the last known answer indefinitely —
        would let an outage during a revoke leave a room collecting for hours."""
        _Facade.raises = True
        conn = await _drive(callbacks)

        await callbacks["on_client_message"](
            conn, {"type": "draft.update", "surface": "composer", "content": "unsent"}
        )

        assert _RecordingDraftStore.puts == []

    async def test_a_clear_is_honoured_even_with_no_grant(self, callbacks: dict[str, Any]) -> None:
        """A revoke between the write and the send would otherwise strand an entry
        until its TTL — the one case where the participant explicitly asked for it
        to go."""
        _Facade.grant = False
        conn = await _drive(callbacks)

        await callbacks["on_client_message"](conn, {"type": "draft.clear", "surface": "composer"})

        assert len(_RecordingDraftStore.clears) == 1

    async def test_the_grant_is_not_read_once_per_frame(
        self, callbacks: dict[str, Any], stopped_clock: None
    ) -> None:
        """§7's performance claim, as a test rather than an assertion in prose: a
        30-typist room must cost 30 grant reads for the lesson, not ~15 sessions a
        second on the socket path."""
        conn = await _drive(callbacks)

        for _ in range(5):
            await callbacks["on_client_message"](
                conn, {"type": "draft.update", "surface": "composer", "content": "x"}
            )

        assert _Facade.reads == 1

    async def test_a_revoked_grant_stops_new_writes_at_the_next_window(
        self, callbacks: dict[str, Any]
    ) -> None:
        """The behaviour that replaced the non-existent `chatroom.agents_changed`
        event. The clock advances past the window between frames, so the second
        update re-resolves and finds the grant gone."""
        conn = await _drive(callbacks)
        await callbacks["on_client_message"](
            conn, {"type": "draft.update", "surface": "composer", "content": "a"}
        )

        _Facade.grant = False
        await callbacks["on_client_message"](
            conn, {"type": "draft.update", "surface": "composer", "content": "b"}
        )

        assert len(_RecordingDraftStore.puts) == 1
        assert _Facade.reads == 2

    async def test_a_grant_added_mid_session_starts_collecting(self, callbacks: dict[str, Any]) -> None:
        """The same window in the other direction, so a teacher who grants mid-class
        does not have to make everyone reload."""
        _Facade.grant = False
        conn = await _drive(callbacks)
        await callbacks["on_client_message"](
            conn, {"type": "draft.update", "surface": "composer", "content": "a"}
        )

        _Facade.grant = True
        await callbacks["on_client_message"](
            conn, {"type": "draft.update", "surface": "composer", "content": "b"}
        )

        assert len(_RecordingDraftStore.puts) == 1


class TestTheThrottle:
    """AC-3's server half."""

    async def test_a_second_update_inside_the_window_is_dropped(
        self, callbacks: dict[str, Any], stopped_clock: None
    ) -> None:
        conn = await _drive(callbacks)

        await callbacks["on_client_message"](
            conn, {"type": "draft.update", "surface": "composer", "content": "a"}
        )
        await callbacks["on_client_message"](
            conn, {"type": "draft.update", "surface": "composer", "content": "b"}
        )

        assert len(_RecordingDraftStore.puts) == 1

    async def test_a_clear_is_never_throttled(self, callbacks: dict[str, Any], stopped_clock: None) -> None:
        """Dropping a clear leaves unsent text readable that its author has just
        sent or discarded, which is the opposite of what the throttle is for."""
        conn = await _drive(callbacks)

        await callbacks["on_client_message"](
            conn, {"type": "draft.update", "surface": "composer", "content": "a"}
        )
        await callbacks["on_client_message"](conn, {"type": "draft.clear", "surface": "composer"})
        await callbacks["on_client_message"](conn, {"type": "draft.clear", "surface": "composer"})

        assert len(_RecordingDraftStore.clears) == 2

    async def test_a_draft_frame_does_not_consume_the_typing_window(
        self, callbacks: dict[str, Any], stopped_clock: None
    ) -> None:
        """The two frames ride the same client burst timer, so a shared throttle
        variable would let whichever landed first swallow the other — the typing
        indicator would go dark in exactly the rooms that report drafts."""
        conn = await _drive(callbacks)

        await callbacks["on_client_message"](
            conn, {"type": "draft.update", "surface": "composer", "content": "a"}
        )
        await callbacks["on_client_message"](conn, {"type": "typing.start"})

        assert [e for e, _ in _RecordingPublisher.emitted] == ["typing.start"]


class TestMalformedFramesAreDroppedInSilence:
    @pytest.mark.parametrize(
        "frame",
        [
            {"type": "draft.update", "content": "x"},
            {"type": "draft.update", "surface": "message", "content": "x"},
            {"type": "draft.update", "surface": None, "content": "x"},
            {"type": "draft.update", "surface": "activity", "content": "x"},
            {"type": "draft.update", "surface": "activity", "key": "a:b", "content": "x"},
            {"type": "draft.update", "surface": "composer"},
            {"type": "draft.update", "surface": "composer", "content": 42},
            {"type": "draft.update", "surface": "composer", "content": {"nested": "x"}},
        ],
    )
    async def test_nothing_is_stored_and_nothing_is_answered(
        self, callbacks: dict[str, Any], frame: dict[str, Any]
    ) -> None:
        conn = await _drive(callbacks)

        await callbacks["on_client_message"](conn, frame)

        assert _RecordingDraftStore.puts == []
        assert _RecordingPublisher.emitted == []

    async def test_an_activity_draft_needs_its_type_key(self, callbacks: dict[str, Any]) -> None:
        conn = await _drive(callbacks)

        await callbacks["on_client_message"](
            conn,
            {"type": "draft.update", "surface": "activity", "key": "mandala-9grid", "content": "{}"},
        )

        assert _RecordingDraftStore.puts[0]["surface"] == "activity"
        assert _RecordingDraftStore.puts[0]["key"] == "mandala-9grid"


class TestTheWriteIsScopedToTheConnectionsOwnRoomAndUser:
    async def test_the_frame_names_no_room_and_no_user(self, callbacks: dict[str, Any]) -> None:
        """[R32.01]'s tenant property. Neither the room nor the subject is taken from
        the frame — both come from the authenticated connection — so there is no
        argument a client could point at another room or another participant."""
        conn = await _drive(callbacks)

        await callbacks["on_client_message"](
            conn,
            {
                "type": "draft.update",
                "surface": "composer",
                "content": "x",
                "room_id": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
            },
        )

        written = _RecordingDraftStore.puts[0]
        assert written["user_id"] == conn.principal.user_id
        assert set(written) == {"room_id", "user_id", "surface", "key", "content"}
