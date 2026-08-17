"""Route behavior for activity activation lifecycle notifications."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import activities
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivityActivationEndResult,
    ActivitySession,
    ActivitySessionCompletionResult,
    SessionStatus,
)


@pytest.mark.parametrize(("transitioned", "expected_dispatches"), [(False, 0), (True, 1)])
async def test_end_broadcasts_only_when_the_activation_transitions(
    monkeypatch: pytest.MonkeyPatch,
    transitioned: bool,
    expected_dispatches: int,
) -> None:
    chatroom_id = uuid.uuid4()
    activation = ActivityActivation(
        id=uuid.uuid4(),
        chatroom_id=chatroom_id,
        activity_type_id=uuid.uuid4(),
        started_by_user_id=uuid.uuid4(),
        status=ActivationStatus.ENDED,
        created_at=dt.datetime(2026, 7, 13, tzinfo=dt.UTC),
        ended_at=dt.datetime(2026, 7, 13, tzinfo=dt.UTC),
    )
    facade = MagicMock()
    facade.end_activation = AsyncMock(
        return_value=ActivityActivationEndResult(activation=activation, transitioned=transitioned)
    )
    facade.get_type = AsyncMock(return_value=None)
    db = MagicMock()
    db.commit = AsyncMock()
    dispatch = AsyncMock()

    monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
    monkeypatch.setattr(activities, "resolve_room_access", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(activities, "ensure_room_creator", MagicMock())
    monkeypatch.setattr(activities, "dispatch_activation_ended", dispatch)

    result = await activities.end_activity_activation(
        chatroom_id=chatroom_id,
        activation_id=activation.id,
        ctx=SimpleNamespace(actor_ip=None, request_id=None),
        principal=SimpleNamespace(user_id=uuid.uuid4()),
        db=db,
    )

    assert result.id == activation.id
    assert dispatch.await_count == expected_dispatches


_NOW = dt.datetime(2026, 8, 17, tzinfo=dt.UTC)


def _activation(chatroom_id: uuid.UUID, started_by: uuid.UUID) -> ActivityActivation:
    return ActivityActivation(
        id=uuid.uuid4(),
        chatroom_id=chatroom_id,
        activity_type_id=uuid.uuid4(),
        started_by_user_id=started_by,
        status=ActivationStatus.ACTIVE,
        created_at=_NOW,
    )


def _session(activation: ActivityActivation, subject: uuid.UUID) -> ActivitySession:
    return ActivitySession(
        id=uuid.uuid4(),
        activity_type_id=activation.activity_type_id,
        chatroom_id=activation.chatroom_id,
        subject_user_id=subject,
        status=SessionStatus.OPEN,
        created_at=_NOW,
        activation_id=activation.id,
        completed_at=_NOW,
    )


class TestCompletionRoute:
    """PATCH .../activity-activations/{id}/completion ([R30.22], AC-5/AC-6/AC-7)."""

    @staticmethod
    def _wire(
        monkeypatch: pytest.MonkeyPatch, *, transitioned: bool
    ) -> tuple[MagicMock, AsyncMock, ActivityActivation, ActivitySession, uuid.UUID]:
        chatroom_id = uuid.uuid4()
        subject = uuid.uuid4()
        activation = _activation(chatroom_id, started_by=uuid.uuid4())
        session = _session(activation, subject)
        facade = MagicMock()
        facade.set_session_completion = AsyncMock(
            return_value=ActivitySessionCompletionResult(
                session=session, activation=activation, transitioned=transitioned
            )
        )
        dispatch = AsyncMock()
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(
            activities,
            "resolve_room_access",
            AsyncMock(return_value=SimpleNamespace(project_id=uuid.uuid4())),
        )
        monkeypatch.setattr(activities, "ensure_can_send", MagicMock())
        monkeypatch.setattr(activities, "_dispatch_activation_progress", dispatch)
        return facade, dispatch, activation, session, subject

    async def test_a_member_may_only_declare_for_themselves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-7: the subject constraint is server-side.

        The body names *another* subject on purpose — a body naming nobody proves
        nothing, because the `or principal.user_id` fallback satisfies the
        assertion by itself. What has to hold is that the route forwards the
        caller as `caller_user_id` regardless, which is what makes the service's
        `_ensure_subject_is_caller` refuse it (404).
        """
        facade, _dispatch, activation, _session, _subject = self._wire(monkeypatch, transitioned=True)
        caller, someone_else = uuid.uuid4(), uuid.uuid4()
        db = MagicMock(commit=AsyncMock())

        await activities.set_activity_session_completion(
            body=activities.ActivitySessionCompletionIn(completed=True, subject_user_id=someone_else),
            chatroom_id=activation.chatroom_id,
            activation_id=activation.id,
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
            principal=SimpleNamespace(user_id=caller, is_admin=False),
            db=db,
        )

        kwargs = facade.set_session_completion.await_args.kwargs
        # The route does not sanitise the subject — it constrains it. Both values
        # reach the service, which is where the mismatch becomes a 404.
        assert kwargs["subject_user_id"] == someone_else
        assert kwargs["caller_user_id"] == caller
        assert kwargs["completed"] is True
        db.commit.assert_awaited_once()

    async def test_a_member_declaring_for_themselves_needs_no_body_subject(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ordinary case the panel produces: no subject in the body at all."""
        facade, _dispatch, activation, _session, _subject = self._wire(monkeypatch, transitioned=True)
        caller = uuid.uuid4()

        await activities.set_activity_session_completion(
            body=activities.ActivitySessionCompletionIn(completed=True, subject_user_id=None),
            chatroom_id=activation.chatroom_id,
            activation_id=activation.id,
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
            principal=SimpleNamespace(user_id=caller, is_admin=False),
            db=MagicMock(commit=AsyncMock()),
        )

        kwargs = facade.set_session_completion.await_args.kwargs
        assert kwargs["subject_user_id"] == caller
        assert kwargs["caller_user_id"] == caller

    async def test_the_admin_arm_lifts_the_subject_constraint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        facade, _dispatch, activation, _session, _subject = self._wire(monkeypatch, transitioned=True)
        target = uuid.uuid4()

        await activities.set_activity_session_completion(
            body=activities.ActivitySessionCompletionIn(completed=False, subject_user_id=target),
            chatroom_id=activation.chatroom_id,
            activation_id=activation.id,
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=True),
            db=MagicMock(commit=AsyncMock()),
        )

        kwargs = facade.set_session_completion.await_args.kwargs
        assert kwargs["subject_user_id"] == target
        assert kwargs["caller_user_id"] is None

    @pytest.mark.parametrize(("transitioned", "expected"), [(False, 0), (True, 1)])
    async def test_it_broadcasts_only_on_a_real_transition(
        self, monkeypatch: pytest.MonkeyPatch, transitioned: bool, expected: int
    ) -> None:
        """A repeat click must not tell the facilitator a count moved."""
        _facade, dispatch, activation, _session, _subject = self._wire(monkeypatch, transitioned=transitioned)

        out = await activities.set_activity_session_completion(
            body=activities.ActivitySessionCompletionIn(completed=True, subject_user_id=None),
            chatroom_id=activation.chatroom_id,
            activation_id=activation.id,
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
            db=MagicMock(commit=AsyncMock()),
        )

        assert dispatch.await_count == expected
        assert out.activation_id == activation.id
        assert out.completed_at is not None

    async def test_the_progress_event_goes_to_the_starter_not_the_room(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The counts are the facilitator's view of the class: in a two-person
        group, broadcasting them would identify the other participant."""
        activation = _activation(uuid.uuid4(), started_by=uuid.uuid4())
        facade = MagicMock()
        facade.count_activation_sessions = AsyncMock(return_value=(2, 3))
        publisher = MagicMock()
        publisher.return_value.emit = AsyncMock()
        monkeypatch.setattr(activities, "Publisher", publisher)
        monkeypatch.setattr(activities, "user_channel", lambda uid: f"ws:user:{uid}")

        await activities._dispatch_activation_progress(facade, activation)

        assert publisher.call_args.args[0] == f"ws:user:{activation.started_by_user_id}"
        event, payload = publisher.return_value.emit.await_args.args
        assert event == "activity.session.completion"
        assert payload["completed"] == 2
        assert payload["in_progress"] == 3
        # No identity on the wire, only counts.
        assert "subject_user_id" not in payload

    async def test_a_publish_failure_never_fails_a_committed_declaration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        activation = _activation(uuid.uuid4(), started_by=uuid.uuid4())
        facade = MagicMock()
        facade.count_activation_sessions = AsyncMock(side_effect=RuntimeError("redis down"))

        await activities._dispatch_activation_progress(facade, activation)


class TestProgressRoute:
    """GET .../activity-activations/{id}/progress ([R30.22], AC-6/AC-7)."""

    async def test_it_is_gated_on_room_creator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        chatroom_id, activation_id = uuid.uuid4(), uuid.uuid4()
        facade = MagicMock()
        facade.count_activation_sessions = AsyncMock(return_value=(4, 1))
        creator_gate = MagicMock()
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(activities, "resolve_room_access", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(activities, "ensure_room_creator", creator_gate)
        # The send floor must not be what guards this read.
        monkeypatch.setattr(
            activities, "ensure_can_send", MagicMock(side_effect=AssertionError("wrong gate"))
        )

        out = await activities.get_activity_activation_progress(
            chatroom_id=chatroom_id,
            activation_id=activation_id,
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
            db=MagicMock(),
        )

        creator_gate.assert_called_once()
        assert (out.completed, out.in_progress) == (4, 1)
        facade.count_activation_sessions.assert_awaited_once_with(
            chatroom_id=chatroom_id, activation_id=activation_id
        )
