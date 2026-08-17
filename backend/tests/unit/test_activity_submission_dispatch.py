"""An activity submission re-arms the silence clock, and does nothing more.

The submit route's post-commit fan-out had three concerns -- a realtime emit, the
validation enqueue and the workflow signal -- and not a fourth, so the message the
transaction had just written was never announced to the wake-up system. Nothing
touched ``touch_silence_timestamp``, so an agent configured to speak "on a lull"
treated a silent worksheet phase as one and barged into it.

Both halves of the fix are pinned here, and the second matters as much as the
first:

- **Positive**: submitting re-arms the clock for every bound agent.
- **Negative**: submitting does **not** run a full wake-up evaluation. The shipped
  teacher agent carries ``every_n_messages {enabled: true, n: 1}``, so counting
  submissions would produce one agent turn per submission -- up to 28 in a class
  of 28, on the teacher's own provider key. That is worse than the defect. The
  trap worth naming: calling ``evaluate_message_wakeups`` and *discarding* its
  result is not a safe middle ground either, because it still runs
  ``increment_message_count`` and drifts every ``n > 1`` agent off its cadence
  against real chat messages.

There was no test for ``_dispatch_submission`` or the submissions route at all
before this file, which is why a missing side effect in the dispatcher went
unnoticed (see the dossier's FU-3).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.api.v1.activities as activities_api


@pytest.fixture
def submission() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        activity_type_id=uuid.uuid4(),
        validation_status=SimpleNamespace(value="validated"),
    )


def _silence_the_other_concerns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the emit, the two enqueues and the progress fan-out."""

    class _Publisher:
        def __init__(self, _channel: object) -> None:
            pass

        async def emit(self, *_args: object, **_kwargs: object) -> None:
            return None

    monkeypatch.setattr(activities_api, "Publisher", _Publisher)
    monkeypatch.setattr(activities_api, "room_channel", lambda _r: "room")
    monkeypatch.setattr(activities_api, "_dispatch_room_activation_progress", AsyncMock())


class TestSubmissionReArmsTheSilenceClock:
    async def test_dispatch_re_arms_the_clock_for_the_room(
        self, monkeypatch: pytest.MonkeyPatch, submission: SimpleNamespace
    ) -> None:
        _silence_the_other_concerns(monkeypatch)
        monkeypatch.setattr(activities_api, "enqueue", AsyncMock())
        room_id = uuid.uuid4()
        facade = AsyncMock()
        monkeypatch.setattr(activities_api, "ConversationFacade", lambda _db: facade)

        await activities_api._dispatch_submission(room_id, submission, {}, db=object())

        facade.note_room_activity.assert_awaited_once_with(chatroom_id=room_id)

    async def test_dispatch_does_not_run_a_full_wakeup_evaluation(
        self, monkeypatch: pytest.MonkeyPatch, submission: SimpleNamespace
    ) -> None:
        """Q-1: submissions re-arm the clock, they do not count as messages.

        If this ever starts failing because someone wired the full evaluation in,
        read the module docstring before "fixing" it -- the 28-turn storm is the
        reason it is not wired in.
        """
        _silence_the_other_concerns(monkeypatch)
        enqueued = AsyncMock()
        monkeypatch.setattr(activities_api, "enqueue", enqueued)
        monkeypatch.setattr(activities_api, "ConversationFacade", lambda _db: AsyncMock())

        await activities_api._dispatch_submission(uuid.uuid4(), submission, {}, db=object())

        assert not any(call.args and call.args[0] == "wakeup_agent" for call in enqueued.await_args_list), (
            "a submission must not enqueue an agent turn"
        )

    async def test_the_re_arm_is_issued_after_both_enqueues(
        self, monkeypatch: pytest.MonkeyPatch, submission: SimpleNamespace
    ) -> None:
        """The re-arm is the only step here that costs a DB read, and it is
        best-effort, so nothing the participant or the workers wait on may queue
        behind it. Ordering is otherwise invisible -- every assertion in this file
        passes with the block in either position -- so it is pinned explicitly.

        Matters under load: a class submitting together would push every
        validation job back by one SELECT each.
        """
        _silence_the_other_concerns(monkeypatch)
        submission.validation_status = SimpleNamespace(value="pending")
        seen: list[str] = []

        async def _enqueue(name: str, *_args: object) -> None:
            seen.append(name)

        facade = AsyncMock()

        async def _note(**_kwargs: object) -> None:
            seen.append("note_room_activity")

        facade.note_room_activity.side_effect = _note
        monkeypatch.setattr(activities_api, "enqueue", _enqueue)
        monkeypatch.setattr(activities_api, "ConversationFacade", lambda _db: facade)

        await activities_api._dispatch_submission(uuid.uuid4(), submission, {}, db=object())

        assert seen == ["validate_activity_submission", "workflow_signal", "note_room_activity"]


class TestSubmissionRepublishesTheFacilitatorCounts:
    """A submission moves the counts, so it has to say so ([R30.22]).

    Two ways, and neither was broadcast before this: the first submission opens
    the subject's session (in_progress 0 -> 1), and any submission retracts an
    "I am finished" declaration. Without the dispatch the facilitator's panel
    keeps showing a class as finished while it carries on working, for the rest
    of the round — the starter has no poll to recover with.
    """

    async def test_dispatch_republishes_the_progress(
        self, monkeypatch: pytest.MonkeyPatch, submission: SimpleNamespace
    ) -> None:
        _silence_the_other_concerns(monkeypatch)
        progress = AsyncMock()
        monkeypatch.setattr(activities_api, "_dispatch_room_activation_progress", progress)
        monkeypatch.setattr(activities_api, "enqueue", AsyncMock())
        monkeypatch.setattr(activities_api, "ConversationFacade", lambda _db: AsyncMock())
        monkeypatch.setattr(activities_api, "ActivitiesFacade", lambda _db: "facade")
        room_id = uuid.uuid4()

        await activities_api._dispatch_submission(room_id, submission, {}, db=object())

        progress.assert_awaited_once_with("facade", room_id)

    async def test_it_publishes_nothing_once_the_round_has_ended(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The facilitator ending the round between commit and dispatch is
        precisely when there is nothing to report."""
        publisher = AsyncMock()
        monkeypatch.setattr(activities_api, "Publisher", publisher)
        facade = AsyncMock()
        facade.get_active_activation.return_value = None

        await activities_api._dispatch_room_activation_progress(facade, uuid.uuid4())

        publisher.assert_not_called()

    async def test_a_failed_lookup_never_surfaces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The submission is already committed; a progress read that cannot run
        must not turn it into a failed request."""
        publisher = AsyncMock()
        monkeypatch.setattr(activities_api, "Publisher", publisher)
        facade = AsyncMock()
        facade.get_active_activation.side_effect = RuntimeError("db gone")

        await activities_api._dispatch_room_activation_progress(facade, uuid.uuid4())

        publisher.assert_not_called()

    async def test_a_failing_re_arm_never_fails_the_submission(
        self, monkeypatch: pytest.MonkeyPatch, submission: SimpleNamespace
    ) -> None:
        """The submission is already committed, so this fan-out is best-effort --
        matching the emit and the two enqueues beside it."""
        _silence_the_other_concerns(monkeypatch)
        monkeypatch.setattr(activities_api, "enqueue", AsyncMock())
        facade = AsyncMock()
        facade.note_room_activity.side_effect = RuntimeError("redis is down")
        monkeypatch.setattr(activities_api, "ConversationFacade", lambda _db: facade)

        await activities_api._dispatch_submission(uuid.uuid4(), submission, {}, db=object())

    async def test_the_other_three_concerns_still_run(
        self, monkeypatch: pytest.MonkeyPatch, submission: SimpleNamespace
    ) -> None:
        """Guard against the new concern displacing an existing one."""
        _silence_the_other_concerns(monkeypatch)
        enqueued = AsyncMock()
        monkeypatch.setattr(activities_api, "enqueue", enqueued)
        monkeypatch.setattr(activities_api, "ConversationFacade", lambda _db: AsyncMock())
        submission.validation_status = SimpleNamespace(value="pending")

        await activities_api._dispatch_submission(uuid.uuid4(), submission, {"k": "v"}, db=object())

        names = [call.args[0] for call in enqueued.await_args_list if call.args]
        assert "validate_activity_submission" in names
        assert "workflow_signal" in names
