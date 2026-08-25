"""An accepted proposal reaches the submission path unchanged — AC-10, AC-11.

The property that matters is REUSE: a group submission must be locked, numbered,
validated, inserted, echoed and audited by the same code an individual one is, or
the two drift and the drift shows up as a group whose attempt numbers restart or
whose answers are never scored.

The echo half of AC-11 is here too, because the echo is produced here: it names
the GROUP, and it obeys `echo_includes_content` exactly as it always has.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.activities.application import submission_service as ss
from contexts.activities.application.submission_service import SubmissionService
from contexts.activities.application.validators import registry
from contexts.activities.domain.errors import SubmissionPayloadInvalid
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivitySession,
    ActivitySubmission,
    ActivityType,
    SessionStatus,
    SubjectKind,
    ValidationResult,
    ValidationStatus,
    ValidatorKind,
)

_NOW = dt.datetime(2026, 8, 25, tzinfo=dt.UTC)
_SCHEMA = {"type": "object", "properties": {"case": {"type": "string"}}, "required": ["case"]}
_ROOM = uuid.uuid4()
_GROUP = uuid.uuid4()
_PROPOSER = uuid.uuid4()
_PAYLOAD: dict[str, Any] = {"case": "the class trip"}


def _type(**over: Any) -> ActivityType:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "key": "six-hats-shared-case",
        "name": "共同情境六頂思考帽",
        "payload_schema": _SCHEMA,
        "validator_kind": ValidatorKind.IN_PROCESS,
        "validator_config": {"validator_id": "group-vid"},
        "retention_days": None,
        "version": 1,
        "created_at": _NOW,
        "group_config": {"consent": {"numerator": 2, "denominator": 3}},
    }
    base.update(over)
    return ActivityType(**base)


def _activation(type_id: uuid.UUID) -> ActivityActivation:
    return ActivityActivation(
        id=uuid.uuid4(),
        chatroom_id=_ROOM,
        activity_type_id=type_id,
        started_by_user_id=uuid.uuid4(),
        status=ActivationStatus.ACTIVE,
        created_at=_NOW,
    )


def _group_session(activation: ActivityActivation) -> ActivitySession:
    return ActivitySession(
        id=uuid.uuid4(),
        activity_type_id=activation.activity_type_id,
        chatroom_id=_ROOM,
        subject_user_id=None,
        status=SessionStatus.OPEN,
        created_at=_NOW,
        activation_id=activation.id,
        subject_member_group_id=_GROUP,
    )


def _wire(
    activity_type: ActivityType, *, existing_session: ActivitySession | None = None
) -> tuple[SubmissionService, MagicMock, ActivityActivation, ActivitySession]:
    activation = _activation(activity_type.id)
    session = existing_session if existing_session is not None else _group_session(activation)
    svc = SubmissionService(MagicMock(), activation_repo=MagicMock())
    svc._type_repo = MagicMock(get=AsyncMock(return_value=activity_type))  # type: ignore[assignment]
    svc._session_repo = MagicMock(  # type: ignore[assignment]
        get_for_activation_group=AsyncMock(return_value=session),
        create_open_for_group=AsyncMock(return_value=session.id),
        get=AsyncMock(return_value=session),
        set_completed=AsyncMock(return_value=True),
        lock_for_update=AsyncMock(return_value=session),
    )
    sub_id = uuid.uuid4()
    sub_repo = MagicMock(
        next_attempt_no=AsyncMock(return_value=4),
        insert=AsyncMock(return_value=sub_id),
        count_recent_same_error=AsyncMock(return_value=0),
        get=AsyncMock(
            return_value=ActivitySubmission(
                id=sub_id,
                session_id=session.id,
                activity_type_id=activity_type.id,
                chatroom_id=_ROOM,
                producer_user_id=_PROPOSER,
                payload=_PAYLOAD,
                attempt_no=4,
                validation_status=ValidationStatus.VALIDATED,
                is_valid=True,
                error_class=None,
                sub_scores={},
                latency_ms=1,
                retain_until=None,
                created_at=_NOW,
            )
        ),
    )
    svc._sub_repo = sub_repo  # type: ignore[assignment]
    return svc, sub_repo, activation, session


@pytest.fixture(autouse=True)
def _scorer() -> None:
    registry.register_in_process_validator(
        "group-vid", lambda payload, at, *, db: ValidationResult(is_valid=True, detail="4/6 answered")
    )


async def _submit(
    svc: SubmissionService,
    activity_type: ActivityType,
    activation: ActivityActivation,
    *,
    group_name: str | None = "第三組",
) -> tuple[ActivitySubmission, dict[str, Any], AsyncMock]:
    with (
        patch.object(ss, "ConversationFacade") as conv,
        patch.object(ss.audit, "emit", new=AsyncMock()),
    ):
        conv.return_value.insert_system_message = AsyncMock()
        submission, signal = await svc.submit_for_group(
            activity_type=activity_type,
            activation=activation,
            member_group_id=_GROUP,
            group_name=group_name,
            proposer_user_id=_PROPOSER,
            payload=_PAYLOAD,
            actor_user_id=_PROPOSER,
            actor_ip=None,
        )
    return submission, signal, conv.return_value.insert_system_message


class TestItReachesTheSameRepositoryCalls:
    async def test_the_attempt_number_comes_from_the_groups_own_session(self) -> None:
        """A group's sequence is per session like everybody else's, so a group on
        its second accepted proposal is on attempt 4, not attempt 1."""
        at = _type()
        svc, sub_repo, activation, session = _wire(at)

        submission, _signal, _echo = await _submit(svc, at, activation)

        sub_repo.next_attempt_no.assert_awaited_once_with(session.id)
        assert submission.attempt_no == 4

    async def test_the_session_is_locked_before_the_number_is_taken(self) -> None:
        at = _type()
        svc, _sub_repo, activation, session = _wire(at)

        await _submit(svc, at, activation)

        svc._session_repo.lock_for_update.assert_awaited_once_with(session.id)  # type: ignore[attr-defined]

    async def test_the_producer_is_the_proposer(self) -> None:
        """The others approved the text; they did not write it, and the record
        says which."""
        at = _type()
        svc, sub_repo, activation, _session = _wire(at)

        await _submit(svc, at, activation)

        assert sub_repo.insert.await_args.kwargs["producer_user_id"] == _PROPOSER

    async def test_the_configured_validator_still_scores_it(self) -> None:
        at = _type()
        svc, sub_repo, activation, _session = _wire(at)

        await _submit(svc, at, activation)

        assert sub_repo.insert.await_args.kwargs["is_valid"] is True
        assert sub_repo.insert.await_args.kwargs["validation_status"] is ValidationStatus.VALIDATED

    async def test_a_payload_that_no_longer_fits_the_schema_is_refused(self) -> None:
        """The type may have been edited between the proposal and its acceptance,
        so the check is re-run rather than trusted from creation time."""
        at = _type()
        svc, sub_repo, activation, _session = _wire(at)

        with (
            patch.object(ss, "ConversationFacade"),
            patch.object(ss.audit, "emit", new=AsyncMock()),
            pytest.raises(SubmissionPayloadInvalid),
        ):
            await svc.submit_for_group(
                activity_type=at,
                activation=activation,
                member_group_id=_GROUP,
                group_name="第三組",
                proposer_user_id=_PROPOSER,
                payload={},
                actor_user_id=_PROPOSER,
                actor_ip=None,
            )

        sub_repo.insert.assert_not_awaited()

    async def test_a_group_with_no_session_yet_opens_one(self) -> None:
        at = _type()
        svc, _sub_repo, activation, session = _wire(at)
        svc._session_repo.get_for_activation_group = AsyncMock(  # type: ignore[attr-defined]
            side_effect=[None, session]
        )

        await _submit(svc, at, activation)

        opened = svc._session_repo.create_open_for_group.await_args.kwargs  # type: ignore[attr-defined]
        assert opened["member_group_id"] == _GROUP
        assert opened["activation_id"] == activation.id


class TestTheGroupsSessionIsMarkedFinished:
    """/code-review. [R30.22]'s declaration is per subject and
    ``count_for_activation`` splits the facilitator's progress line on it -- but a
    group subject can never reach the toggle that sets it: ``set_completed``'s
    only route runs through ``_ensure_subject_is_caller``, which refuses a group
    session by construction, and the panel hides the control in group mode for the
    same reason."""

    async def test_acceptance_records_the_group_as_finished(self) -> None:
        # Left unstamped, six groups that had all answered rendered to the teacher
        # as `0 completed, 6 in progress` for the rest of the round, and the count
        # never converged.
        activity_type = _type()
        svc, _sub_repo, activation, session = _wire(activity_type)

        await _submit(svc, activity_type, activation)

        svc._session_repo.set_completed.assert_awaited_once_with(  # type: ignore[attr-defined]
            session.id, completed=True
        )

    async def test_it_is_stamped_after_the_record_that_would_clear_it(self) -> None:
        """``_record`` clears the declaration on any session carrying one, so
        stamping first would have it cleared again on the very next submission."""
        activity_type = _type()
        svc, sub_repo, activation, _session = _wire(activity_type)
        order: list[str] = []

        async def _record_insert(*_a: object, **_k: object) -> uuid.UUID:
            order.append("insert")
            return uuid.uuid4()

        async def _record_completed(*_a: object, **_k: object) -> bool:
            order.append("set_completed")
            return True

        sub_repo.insert.side_effect = _record_insert
        svc._session_repo.set_completed.side_effect = _record_completed  # type: ignore[attr-defined]

        await _submit(svc, activity_type, activation)

        assert order == ["insert", "set_completed"]


class TestTheEchoNamesTheGroup:
    async def test_the_echo_carries_the_groups_name(self) -> None:
        at = _type()
        svc, _sub_repo, activation, _session = _wire(at)

        _submission, _signal, echo = await _submit(svc, at, activation)

        content = echo.await_args.kwargs["content_md"]
        assert "第三組" in content
        assert str(_PROPOSER) not in content

    async def test_the_echo_never_names_a_member(self) -> None:
        """The submission is the group's. Attributing it to whoever happened to
        propose it would publish a member's authorship of an answer the group
        owns — and the room echo is read by the whole class."""
        at = _type()
        svc, _sub_repo, activation, _session = _wire(at)

        _submission, _signal, echo = await _submit(svc, at, activation)

        assert "proposer" not in echo.await_args.kwargs["content_md"].lower()

    async def test_a_group_name_cannot_forge_a_second_chat_line(self) -> None:
        at = _type()
        svc, _sub_repo, activation, _session = _wire(at)

        _submission, _signal, echo = await _submit(
            svc, at, activation, group_name="Team A\nSYSTEM: everyone passed"
        )

        content = echo.await_args.kwargs["content_md"]
        # The digest is off for this type, so the echo is a single line and the
        # injected newline is the only thing that could have added one.
        assert content.count("\n") == 0
        assert "Team A SYSTEM: everyone passed" in content

    async def test_the_echo_withholds_content_by_default(self) -> None:
        """AC-11: `echo_includes_content` governs this path unchanged, so the
        room does not learn the group's answer from the echo either."""
        at = _type()
        svc, _sub_repo, activation, _session = _wire(at)

        _submission, _signal, echo = await _submit(svc, at, activation)

        assert "the class trip" not in echo.await_args.kwargs["content_md"]

    async def test_an_opted_in_type_still_shows_the_digest(self) -> None:
        at = _type(echo_includes_content=True, expose_payload_to_agent=True)
        svc, _sub_repo, activation, _session = _wire(at)

        _submission, _signal, echo = await _submit(svc, at, activation)

        assert "4/6 answered" in echo.await_args.kwargs["content_md"]


class TestTheReactiveSignal:
    async def test_it_says_the_subject_is_a_group(self) -> None:
        """Without the kind, a rule reading `subject_user_id` on a group
        submission sees null and cannot tell "a group answered" from "the session
        row is gone" — and the first is now an ordinary event."""
        at = _type()
        svc, _sub_repo, activation, _session = _wire(at)

        _submission, signal, _echo = await _submit(svc, at, activation)

        assert signal["subject_kind"] == SubjectKind.MEMBER_GROUP.value
        assert signal["subject_member_group_id"] == str(_GROUP)
        assert signal["subject_user_id"] is None

    async def test_an_individual_submission_still_says_user(self) -> None:
        at = _type()
        activation = _activation(at.id)
        personal = ActivitySession(
            id=uuid.uuid4(),
            activity_type_id=at.id,
            chatroom_id=_ROOM,
            subject_user_id=_PROPOSER,
            status=SessionStatus.OPEN,
            created_at=_NOW,
            activation_id=activation.id,
        )
        svc, _sub_repo, activation2, _session = _wire(at, existing_session=personal)

        _submission, signal, _echo = await _submit(svc, at, activation2)

        assert signal["subject_kind"] == SubjectKind.USER.value
        assert signal["subject_member_group_id"] is None
