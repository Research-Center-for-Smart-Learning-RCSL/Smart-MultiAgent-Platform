"""The post-commit half of an activation notice ([R30.45], AC-12, AC-13).

The service writes the row inside the request transaction; these pin what the
dispatcher does once it is committed: other participants get it live as an
ordinary ``message.created``, and the wake-up system sees room activity through
``note_room_activity``, the narrow path that re-arms silence without counting
toward ``every_n_messages`` or resetting autostop.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from contexts.activities.domain.models import ActivationNotice, ActivationStatus, ActivityActivation
from contexts.activities.interfaces import broadcast

_NOW = dt.datetime(2026, 10, 24, 9, 0, tzinfo=dt.UTC)


def _notice() -> ActivationNotice:
    return ActivationNotice(message_id=uuid.uuid4(), chatroom_id=uuid.uuid4(), created_at=_NOW)


class _Spy:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict]] = []
        self.re_armed: list[uuid.UUID] = []
        self.message_wakeups = 0


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    import contexts.conversation.interfaces.facade as conv_facade
    import shared_kernel.db.session as session_mod

    s = _Spy()

    class _Publisher:
        def __init__(self, channel: str) -> None:
            self.channel = channel

        async def emit(self, event: str, payload: dict) -> None:
            s.published.append((self.channel, event, payload))

    class _Conversation:
        def __init__(self, _db: object) -> None:
            pass

        async def note_room_activity(self, *, chatroom_id: uuid.UUID) -> None:
            s.re_armed.append(chatroom_id)

        async def evaluate_message_wakeups(self, *a: Any, **k: Any) -> None:
            s.message_wakeups += 1

    @contextlib.asynccontextmanager
    async def _session() -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(broadcast, "Publisher", _Publisher)
    monkeypatch.setattr(broadcast, "room_channel", lambda rid: f"ws:room:{rid}")
    monkeypatch.setattr(conv_facade, "ConversationFacade", _Conversation)
    monkeypatch.setattr(session_mod, "async_session", _session)
    return s


class TestDispatchActivationNotice:
    async def test_the_room_gets_an_ordinary_system_message(self, spy: _Spy) -> None:
        notice = _notice()
        await broadcast.dispatch_activation_notice(notice)

        assert spy.published == [
            (
                f"ws:room:{notice.chatroom_id}",
                "message.created",
                {
                    "message_id": str(notice.message_id),
                    "sender_type": "system",
                    "sender_id": None,
                    "created_at": _NOW.isoformat(),
                },
            )
        ]

    async def test_it_re_arms_silence_through_the_narrow_path_only(self, spy: _Spy) -> None:
        notice = _notice()
        await broadcast.dispatch_activation_notice(notice)

        assert spy.re_armed == [notice.chatroom_id]
        assert spy.message_wakeups == 0

    async def test_a_failed_publish_still_re_arms(self, spy: _Spy, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Down:
            def __init__(self, channel: str) -> None:
                pass

            async def emit(self, event: str, payload: dict) -> None:
                raise ConnectionError("redis down")

        monkeypatch.setattr(broadcast, "Publisher", _Down)
        notice = _notice()
        await broadcast.dispatch_activation_notice(notice)

        assert spy.re_armed == [notice.chatroom_id]


class TestTheLifecycleDispatchersCarryTheNotice:
    async def test_a_started_event_with_a_notice_publishes_both(self, spy: _Spy) -> None:
        notice = _notice()
        activation = ActivityActivation(
            id=uuid.uuid4(),
            chatroom_id=notice.chatroom_id,
            activity_type_id=uuid.uuid4(),
            started_by_user_id=uuid.uuid4(),
            status=ActivationStatus.ACTIVE,
            created_at=_NOW,
        )
        await broadcast.dispatch_activation_started(activation, None, notice=notice)

        assert [e for _, e, _ in spy.published] == ["activity.activation.started", "message.created"]

    async def test_a_repeated_start_publishes_no_divider(self, spy: _Spy) -> None:
        activation = ActivityActivation(
            id=uuid.uuid4(),
            chatroom_id=uuid.uuid4(),
            activity_type_id=uuid.uuid4(),
            started_by_user_id=uuid.uuid4(),
            status=ActivationStatus.ACTIVE,
            created_at=_NOW,
        )
        await broadcast.dispatch_activation_started(activation, None, notice=None)

        assert "message.created" not in [e for _, e, _ in spy.published]
        assert spy.re_armed == []

    async def test_an_ended_event_with_a_notice_publishes_both(self, spy: _Spy) -> None:
        notice = _notice()
        await broadcast.dispatch_activation_ended(notice.chatroom_id, uuid.uuid4(), notice=notice)

        assert [e for _, e, _ in spy.published] == ["activity.activation.ended", "message.created"]
        assert spy.re_armed == [notice.chatroom_id]
