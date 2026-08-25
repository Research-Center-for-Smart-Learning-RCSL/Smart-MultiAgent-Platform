"""Unit coverage for the room-level activation lifecycle."""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.activities.application import activation_service
from contexts.activities.application.activation_service import ActivationService
from contexts.activities.domain.errors import ActivityAlreadyActive, ActivityTypeNotFound
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivityActivationEndResult,
    ActivityType,
    ValidatorKind,
)

_NOW = dt.datetime(2026, 7, 13, tzinfo=dt.UTC)


def _type(project_id: uuid.UUID, type_id: uuid.UUID) -> ActivityType:
    return ActivityType(
        id=type_id,
        project_id=project_id,
        key="quiz",
        name="Quiz",
        payload_schema={},
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={"validator_id": "quiz"},
        retention_days=None,
        version=1,
        created_at=_NOW,
    )


def _activation(room_id: uuid.UUID, type_id: uuid.UUID) -> ActivityActivation:
    return ActivityActivation(
        id=uuid.uuid4(),
        chatroom_id=room_id,
        activity_type_id=type_id,
        started_by_user_id=uuid.uuid4(),
        status=ActivationStatus.ACTIVE,
        created_at=_NOW,
    )


def _expirer(expired: list[uuid.UUID] | None = None) -> MagicMock:
    """A ``GroupProposalExpirer`` double.

    Ending a round now also expires the proposals open under it (AC-9 of the
    group-submission dossier), so a test double for `end` has one more
    collaborator than it did. Defaults to "nothing was open", which is what every
    test written before group submissions existed was describing.
    """
    return MagicMock(expire_open_for_activation=AsyncMock(return_value=expired or []))


def _no_policy(svc: ActivationService) -> ActivationService:
    """No platform policy row, so the activation gate falls back to permissive.

    That is the behavior every test here was written against; the gate itself is
    covered in ``test_activity_policy_enforcement.py``.
    """
    svc._policy._repo = MagicMock()
    svc._policy._repo.get_platform = AsyncMock(return_value=None)
    return svc


class TestActivationService:
    async def test_start_is_idempotent_for_the_same_type_and_audits_once(self) -> None:
        project_id, room_id, type_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        active = _activation(room_id, type_id)
        repo = MagicMock(
            create_active=AsyncMock(return_value=None), get_active=AsyncMock(return_value=active)
        )
        svc = _no_policy(
            ActivationService(
                MagicMock(),
                activation_repo=repo,
                type_repo=MagicMock(get=AsyncMock(return_value=_type(project_id, type_id))),
            )
        )

        result = await svc.start(
            project_id=project_id,
            chatroom_id=room_id,
            activity_type_id=type_id,
            started_by_user_id=uuid.uuid4(),
            actor_ip=None,
        )

        assert result == active

    async def test_start_rejects_a_different_active_type(self) -> None:
        project_id, room_id, type_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        repo = MagicMock(
            create_active=AsyncMock(return_value=None),
            get_active=AsyncMock(return_value=_activation(room_id, uuid.uuid4())),
        )
        svc = _no_policy(
            ActivationService(
                MagicMock(),
                activation_repo=repo,
                type_repo=MagicMock(get=AsyncMock(return_value=_type(project_id, type_id))),
            )
        )

        with pytest.raises(ActivityAlreadyActive):
            await svc.start(
                project_id=project_id,
                chatroom_id=room_id,
                activity_type_id=type_id,
                started_by_user_id=uuid.uuid4(),
                actor_ip=None,
            )

    async def test_start_rejects_a_cross_project_type_before_persisting(self) -> None:
        project_id, room_id, type_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        repo = MagicMock(create_active=AsyncMock())
        svc = _no_policy(
            ActivationService(
                MagicMock(),
                activation_repo=repo,
                type_repo=MagicMock(get=AsyncMock(return_value=_type(uuid.uuid4(), type_id))),
            )
        )

        with pytest.raises(ActivityTypeNotFound):
            await svc.start(
                project_id=project_id,
                chatroom_id=room_id,
                activity_type_id=type_id,
                started_by_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        repo.create_active.assert_not_awaited()

    async def test_end_reports_no_transition_when_already_ended(self) -> None:
        room_id, type_id = uuid.uuid4(), uuid.uuid4()
        activation = _activation(room_id, type_id)
        repo = MagicMock(get=AsyncMock(return_value=activation), end=AsyncMock(return_value=False))
        closer = MagicMock(close_open_for_activation=AsyncMock(return_value=0))
        svc = _no_policy(
            ActivationService(
                MagicMock(),
                activation_repo=repo,
                type_repo=MagicMock(),
                session_repo=closer,
            )
        )

        result = await svc.end(
            chatroom_id=room_id,
            activation_id=activation.id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )

        assert result == ActivityActivationEndResult(activation=activation, transitioned=False)
        # A double-end changed nothing, so it must not re-close sessions either —
        # a second sweep would restamp closed_at on rows this call did not end.
        closer.close_open_for_activation.assert_not_awaited()

    async def test_end_closes_the_rounds_sessions_and_records_the_count(self) -> None:
        """AC-4: ending a round leaves nothing answered under it open ([R30.22]).

        This is the whole of the cascade: the facilitator's route, a type delete,
        an admin platform-type delete and a project opt-out all end activations
        through this method, so none of them needs its own copy.
        """
        room_id, type_id = uuid.uuid4(), uuid.uuid4()
        activation = _activation(room_id, type_id)
        repo = MagicMock(
            get=AsyncMock(side_effect=[activation, activation]), end=AsyncMock(return_value=True)
        )
        closer = MagicMock(close_open_for_activation=AsyncMock(return_value=3))
        svc = _no_policy(
            ActivationService(
                MagicMock(),
                activation_repo=repo,
                type_repo=MagicMock(),
                session_repo=closer,
                proposal_repo=_expirer(),
            )
        )

        with patch.object(activation_service.audit, "emit", new=AsyncMock()) as emit:
            result = await svc.end(
                chatroom_id=room_id,
                activation_id=activation.id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        assert result.transitioned is True
        closer.close_open_for_activation.assert_awaited_once_with(activation.id)
        assert emit.await_args.args[1].metadata["sessions_closed"] == "3"

    async def test_end_expires_every_proposal_still_open_under_the_round(self) -> None:
        """AC-9 of the group-submission dossier, and it is correctness rather than
        housekeeping: a proposal that outlived its activation would accept later
        and write a submission into a round the class has already left."""
        room_id, type_id = uuid.uuid4(), uuid.uuid4()
        activation = _activation(room_id, type_id)
        repo = MagicMock(
            get=AsyncMock(side_effect=[activation, activation]), end=AsyncMock(return_value=True)
        )
        closer = MagicMock(close_open_for_activation=AsyncMock(return_value=0))
        expirer = _expirer([uuid.uuid4(), uuid.uuid4()])
        svc = _no_policy(
            ActivationService(
                MagicMock(),
                activation_repo=repo,
                type_repo=MagicMock(),
                session_repo=closer,
                proposal_repo=expirer,
            )
        )

        with patch.object(activation_service.audit, "emit", new=AsyncMock()) as emit:
            await svc.end(
                chatroom_id=room_id,
                activation_id=activation.id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        expirer.expire_open_for_activation.assert_awaited_once_with(activation.id)
        assert emit.await_args.args[1].metadata["proposals_expired"] == "2"

    async def test_a_double_end_does_not_re_expire_proposals(self) -> None:
        """The same guard the session close has: a second end changed nothing, so
        it must not restamp `resolved_at` on rows this call did not resolve."""
        room_id, type_id = uuid.uuid4(), uuid.uuid4()
        activation = _activation(room_id, type_id)
        repo = MagicMock(get=AsyncMock(return_value=activation), end=AsyncMock(return_value=False))
        expirer = _expirer()
        svc = _no_policy(
            ActivationService(
                MagicMock(),
                activation_repo=repo,
                type_repo=MagicMock(),
                session_repo=MagicMock(close_open_for_activation=AsyncMock(return_value=0)),
                proposal_repo=expirer,
            )
        )

        await svc.end(
            chatroom_id=room_id,
            activation_id=activation.id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )

        expirer.expire_open_for_activation.assert_not_awaited()

    async def test_end_closes_sessions_before_it_audits(self) -> None:
        """The count on the trail is a fact about what happened, not a prediction:
        if the close raised, no event claiming a number may have been written."""
        room_id, type_id = uuid.uuid4(), uuid.uuid4()
        activation = _activation(room_id, type_id)
        repo = MagicMock(get=AsyncMock(return_value=activation), end=AsyncMock(return_value=True))
        closer = MagicMock(close_open_for_activation=AsyncMock(side_effect=RuntimeError("close blew up")))
        svc = _no_policy(
            ActivationService(
                MagicMock(),
                activation_repo=repo,
                type_repo=MagicMock(),
                session_repo=closer,
                proposal_repo=_expirer(),
            )
        )

        with (
            patch.object(activation_service.audit, "emit", new=AsyncMock()) as emit,
            pytest.raises(RuntimeError, match="close blew up"),
        ):
            await svc.end(
                chatroom_id=room_id,
                activation_id=activation.id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        emit.assert_not_awaited()


class TestDelegatedActivation:
    """Delegated start/end recording ([R30.37]) — AC-7 and AC-12."""

    async def test_a_delegated_start_records_both_the_granter_and_the_agent(self) -> None:
        """AC-7: the row names the granting teacher as its starting user *and* the
        agent that called the tool. The first keeps the facilitator's progress
        events addressable; the second is what makes the round distinguishable."""
        project_id, room_id, type_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        granter, agent_id = uuid.uuid4(), uuid.uuid4()
        created = ActivityActivation(
            id=uuid.uuid4(),
            chatroom_id=room_id,
            activity_type_id=type_id,
            started_by_user_id=granter,
            status=ActivationStatus.ACTIVE,
            created_at=_NOW,
            started_by_agent_id=agent_id,
        )
        repo = MagicMock(
            create_active=AsyncMock(return_value=created.id),
            get=AsyncMock(return_value=created),
        )
        svc = _no_policy(
            ActivationService(
                MagicMock(),
                activation_repo=repo,
                type_repo=MagicMock(get=AsyncMock(return_value=_type(project_id, type_id))),
            )
        )

        with patch.object(activation_service.audit, "emit", new=AsyncMock()) as emit:
            result = await svc.start(
                project_id=project_id,
                chatroom_id=room_id,
                activity_type_id=type_id,
                started_by_user_id=granter,
                actor_ip=None,
                started_by_agent_id=agent_id,
            )

        assert result.started_by_user_id == granter
        assert result.started_by_agent_id == agent_id
        assert repo.create_active.await_args.kwargs["started_by_agent_id"] == agent_id
        # AC-12: the trail names the agent and says how it acted.
        metadata = emit.await_args.args[1].metadata
        assert metadata["started_by_agent_id"] == str(agent_id)
        assert metadata["via"] == "agent_tool"
        # The actor stays the delegating human: the agent is not an audit principal.
        assert emit.await_args.args[1].actor_user_id == granter

    async def test_a_human_start_records_neither_key(self) -> None:
        """AC-12's negative half. `via` absent, not "facilitator": historical rows
        carry no such claim and this code must not start inventing one."""
        project_id, room_id, type_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        created = _activation(room_id, type_id)
        repo = MagicMock(
            create_active=AsyncMock(return_value=created.id), get=AsyncMock(return_value=created)
        )
        svc = _no_policy(
            ActivationService(
                MagicMock(),
                activation_repo=repo,
                type_repo=MagicMock(get=AsyncMock(return_value=_type(project_id, type_id))),
            )
        )

        with patch.object(activation_service.audit, "emit", new=AsyncMock()) as emit:
            await svc.start(
                project_id=project_id,
                chatroom_id=room_id,
                activity_type_id=type_id,
                started_by_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        metadata = emit.await_args.args[1].metadata
        assert "started_by_agent_id" not in metadata
        assert "via" not in metadata
        assert repo.create_active.await_args.kwargs["started_by_agent_id"] is None

    async def test_a_delegated_end_names_the_ending_agent_not_the_starting_one(self) -> None:
        """An agent may end a round a teacher started, so the ended event must not
        claim that agent started it (D-1 of this feature's dossier)."""
        room_id, type_id = uuid.uuid4(), uuid.uuid4()
        agent_id = uuid.uuid4()
        activation = _activation(room_id, type_id)
        repo = MagicMock(
            get=AsyncMock(side_effect=[activation, activation]), end=AsyncMock(return_value=True)
        )
        closer = MagicMock(close_open_for_activation=AsyncMock(return_value=0))
        svc = _no_policy(
            ActivationService(
                MagicMock(),
                activation_repo=repo,
                type_repo=MagicMock(),
                session_repo=closer,
                proposal_repo=_expirer(),
            )
        )

        with patch.object(activation_service.audit, "emit", new=AsyncMock()) as emit:
            await svc.end(
                chatroom_id=room_id,
                activation_id=activation.id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
                ended_by_agent_id=agent_id,
            )

        metadata = emit.await_args.args[1].metadata
        assert metadata["ended_by_agent_id"] == str(agent_id)
        assert metadata["via"] == "agent_tool"
        assert "started_by_agent_id" not in metadata

    async def test_ending_does_not_overwrite_who_started_the_round(self) -> None:
        """The stored `started_by_agent_id` is a different fact from who ended it."""
        room_id, type_id = uuid.uuid4(), uuid.uuid4()
        starter_agent = uuid.uuid4()
        activation = ActivityActivation(
            id=uuid.uuid4(),
            chatroom_id=room_id,
            activity_type_id=type_id,
            started_by_user_id=uuid.uuid4(),
            status=ActivationStatus.ACTIVE,
            created_at=_NOW,
            started_by_agent_id=starter_agent,
        )
        repo = MagicMock(
            get=AsyncMock(side_effect=[activation, activation]), end=AsyncMock(return_value=True)
        )
        closer = MagicMock(close_open_for_activation=AsyncMock(return_value=0))
        svc = _no_policy(
            ActivationService(
                MagicMock(),
                activation_repo=repo,
                type_repo=MagicMock(),
                session_repo=closer,
                proposal_repo=_expirer(),
            )
        )

        with patch.object(activation_service.audit, "emit", new=AsyncMock()):
            result = await svc.end(
                chatroom_id=room_id,
                activation_id=activation.id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
                ended_by_agent_id=uuid.uuid4(),
            )

        assert result.activation.started_by_agent_id == starter_agent
