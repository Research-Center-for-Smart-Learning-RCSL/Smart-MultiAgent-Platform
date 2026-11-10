"""A valid submission wakes the room's granted teacher, debounced -- AC-10.

Spec: ``docs/tasks/2026-11-10-crat-lab-trial-fixes/spec.md``, milestone 4. In
the lab trial a student's answer woke nobody: only the deadline and "everyone
connected has submitted" did, so the round stalled after each submission. These
pin the window (one wake per activation per 20 seconds, at its end), the skip
rules (invalid or pending, the completion wake already sent, the round ended),
and the two call sites. The closed-room skip is the worker guard shared by every
automatic trigger (``test_agent_trigger_wiring.py``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.api.v1.activities as activities_api
from app.wiring import platform_wakes
from contexts.activities.application import platform_events as pe
from contexts.activities.domain.models import ActivationStatus, ActivityType, ValidatorKind

_ROOM = uuid.uuid4()
_ACTIVATION = uuid.uuid4()
_TYPE = uuid.uuid4()
_SUBMISSION = uuid.uuid4()
_T0 = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.UTC)


def _type() -> ActivityType:
    return ActivityType(
        id=_TYPE,
        project_id=uuid.uuid4(),
        key="crat",
        name="CRAT",
        payload_schema={},
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={},
        retention_days=None,
        version=1,
        created_at=_T0,
    )


def _activation(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": _ACTIVATION,
        "chatroom_id": _ROOM,
        "activity_type_id": _TYPE,
        "status": ActivationStatus.ACTIVE,
        "completion_notified_at": None,
    }
    return SimpleNamespace(**{**base, **over})


class TestTheNotice:
    def test_it_states_only_the_name_and_the_two_counts(self) -> None:
        text = pe.submission_notice(_type(), in_window=2, in_round=5)

        assert (
            text
            == '2 new valid submissions to "CRAT" arrived in the last few seconds; 5 in this round so far.'
        )

    def test_one_submission_is_singular(self) -> None:
        text = pe.submission_notice(_type(), in_window=1, in_round=1)

        assert text.startswith('1 new valid submission to "CRAT"')


class TestTheWindowEvent:
    def _events(self, activation: SimpleNamespace | None, counts: tuple[int, int] = (2, 5)) -> Any:
        events = pe.PlatformActivityEvents(MagicMock())
        events._activations = MagicMock(get=AsyncMock(return_value=activation))
        events._submissions = MagicMock(valid_counts_since=AsyncMock(return_value=counts))
        events._types = MagicMock(get=AsyncMock(return_value=SimpleNamespace(name="CRAT")))
        return events

    async def _event(self, events: Any) -> Any:
        return await events.submission_window_event(
            chatroom_id=_ROOM, activation_id=_ACTIVATION, window_started_at=_T0
        )

    async def test_it_carries_the_window_and_round_counts(self) -> None:
        events = self._events(_activation())

        event = await self._event(events)

        assert event is not None
        assert event.activity_type_id == _TYPE
        assert "2 new valid submissions" in event.notice
        assert "5 in this round" in event.notice
        events._submissions.valid_counts_since.assert_awaited_once_with(activation_id=_ACTIVATION, since=_T0)

    async def test_an_ended_round_wakes_nobody(self) -> None:
        assert await self._event(self._events(_activation(status=ActivationStatus.ENDED))) is None

    async def test_a_round_of_another_room_wakes_nobody(self) -> None:
        assert await self._event(self._events(_activation(chatroom_id=uuid.uuid4()))) is None

    async def test_a_window_with_no_valid_submission_wakes_nobody(self) -> None:
        """An invalid verdict counts for no one, so the window it opened is empty."""
        assert await self._event(self._events(_activation(), counts=(0, 3))) is None

    async def test_a_completion_wake_inside_the_window_covers_it(self) -> None:
        later = _T0 + dt.timedelta(seconds=5)
        assert await self._event(self._events(_activation(completion_notified_at=later))) is None

    async def test_a_completion_wake_before_the_window_does_not(self) -> None:
        """A resubmission after everyone had submitted is still news."""
        earlier = _T0 - dt.timedelta(minutes=3)
        assert await self._event(self._events(_activation(completion_notified_at=earlier))) is not None


class _Redis:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        assert nx is True
        if key in self.keys:
            return False
        self.keys[key] = value
        self.ttl[key] = ex
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.keys.pop(key, None) is not None else 0


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    redis = _Redis()
    enqueue = AsyncMock()
    facade = MagicMock(
        get_submission=AsyncMock(
            return_value=SimpleNamespace(id=_SUBMISSION, chatroom_id=_ROOM, is_valid=True, created_at=_T0)
        ),
        get_active_activation=AsyncMock(return_value=_activation()),
    )
    monkeypatch.setattr(platform_wakes, "get_redis", lambda: redis)
    monkeypatch.setattr(platform_wakes, "enqueue", enqueue)
    monkeypatch.setattr(platform_wakes, "ActivitiesFacade", lambda _db: facade)
    return SimpleNamespace(redis=redis, enqueue=enqueue, facade=facade)


async def _dispatch() -> None:
    await platform_wakes.dispatch_submission_received(MagicMock(), _ROOM, _SUBMISSION)


class TestTheWindowClaim:
    async def test_the_first_submission_opens_a_window_and_defers_the_wake_to_its_end(
        self, wired: SimpleNamespace
    ) -> None:
        await _dispatch()

        wired.enqueue.assert_awaited_once()
        args, kwargs = wired.enqueue.await_args
        assert args == ("submission_received_wake", str(_ROOM), str(_ACTIVATION), _T0.isoformat())
        assert kwargs == {"_defer_by": dt.timedelta(seconds=platform_wakes.SUBMISSION_WAKE_WINDOW_SECONDS)}
        assert platform_wakes.SUBMISSION_WAKE_WINDOW_SECONDS == 20
        assert set(wired.redis.ttl.values()) == {20}

    async def test_several_submissions_in_a_window_schedule_one_wake(self, wired: SimpleNamespace) -> None:
        for _ in range(4):
            await _dispatch()

        assert wired.enqueue.await_count == 1

    async def test_the_window_is_per_activation(self, wired: SimpleNamespace) -> None:
        await _dispatch()
        wired.facade.get_active_activation.return_value = _activation(id=uuid.uuid4())
        await _dispatch()

        assert wired.enqueue.await_count == 2

    async def test_an_invalid_submission_opens_no_window(self, wired: SimpleNamespace) -> None:
        wired.facade.get_submission.return_value = SimpleNamespace(
            id=_SUBMISSION, chatroom_id=_ROOM, is_valid=False, created_at=_T0
        )

        await _dispatch()

        wired.enqueue.assert_not_awaited()
        assert wired.redis.keys == {}

    async def test_a_pending_submission_opens_no_window(self, wired: SimpleNamespace) -> None:
        """Pending is ``is_valid`` false until its verdict; the worker re-dispatches then."""
        wired.facade.get_submission.return_value = SimpleNamespace(
            id=_SUBMISSION, chatroom_id=_ROOM, is_valid=False, created_at=_T0
        )

        await _dispatch()

        wired.enqueue.assert_not_awaited()

    async def test_no_running_round_opens_no_window(self, wired: SimpleNamespace) -> None:
        wired.facade.get_active_activation.return_value = None

        await _dispatch()

        wired.enqueue.assert_not_awaited()

    async def test_a_failed_enqueue_frees_the_window(self, wired: SimpleNamespace) -> None:
        wired.enqueue.side_effect = [RuntimeError("redis down"), None]

        await _dispatch()
        await _dispatch()

        assert wired.enqueue.await_count == 2


class TestTheCallSites:
    @pytest.fixture
    def route(self, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
        class _Publisher:
            def __init__(self, _channel: object) -> None:
                pass

            async def emit(self, *_args: object, **_kwargs: object) -> None:
                return None

        monkeypatch.setattr(activities_api, "Publisher", _Publisher)
        monkeypatch.setattr(activities_api, "room_channel", lambda _r: "room")
        monkeypatch.setattr(activities_api, "dispatch_room_activation_progress", AsyncMock())
        monkeypatch.setattr(activities_api, "enqueue", AsyncMock())
        monkeypatch.setattr(activities_api, "ConversationFacade", lambda _db: AsyncMock())
        completion = AsyncMock(return_value=False)
        received = AsyncMock()
        monkeypatch.setattr(activities_api, "dispatch_all_submitted", completion)
        monkeypatch.setattr(activities_api, "dispatch_submission_received", received)
        return SimpleNamespace(completion=completion, received=received)

    @staticmethod
    def _submission(*, valid: bool = True) -> SimpleNamespace:
        return SimpleNamespace(
            id=_SUBMISSION,
            activity_type_id=_TYPE,
            validation_status=SimpleNamespace(value="validated" if valid else "error"),
            is_valid=valid,
            judge_status=None,
        )

    async def test_a_valid_submission_reaches_the_window(self, route: SimpleNamespace) -> None:
        db = MagicMock()

        await activities_api._dispatch_submission(_ROOM, self._submission(), {}, db=db)

        route.received.assert_awaited_once_with(db, _ROOM, _SUBMISSION)

    async def test_the_submission_that_claimed_the_completion_wake_skips_it(
        self, route: SimpleNamespace
    ) -> None:
        route.completion.return_value = True

        await activities_api._dispatch_submission(_ROOM, self._submission(), {}, db=MagicMock())

        route.received.assert_not_awaited()

    async def test_an_invalid_submission_reaches_neither(self, route: SimpleNamespace) -> None:
        await activities_api._dispatch_submission(_ROOM, self._submission(valid=False), {}, db=MagicMock())

        route.completion.assert_not_awaited()
        route.received.assert_not_awaited()

    async def test_the_window_end_wakes_the_granted_bindings_with_the_trigger(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        event = SimpleNamespace(chatroom_id=_ROOM, activity_type_id=_TYPE, notice="n")
        facade = MagicMock(submission_window_event=AsyncMock(return_value=event))
        wake = AsyncMock(return_value=1)

        class _Session:
            async def __aenter__(self) -> object:
                return "db"

            async def __aexit__(self, *_exc: object) -> None:
                return None

        monkeypatch.setattr(platform_wakes, "async_session", _Session)
        monkeypatch.setattr(platform_wakes, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(platform_wakes, "wake_for_activity_event", wake)

        assert await platform_wakes.run_submission_wake(_ROOM, _ACTIVATION, _T0) == 1
        wake.assert_awaited_once_with("db", event, "submission_received")
        facade.submission_window_event.assert_awaited_once_with(
            chatroom_id=_ROOM, activation_id=_ACTIVATION, window_started_at=_T0
        )
