"""Unit coverage for the room-level activation lifecycle."""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

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
        svc = _no_policy(
            ActivationService(
                MagicMock(),
                activation_repo=repo,
                type_repo=MagicMock(),
            )
        )

        result = await svc.end(
            chatroom_id=room_id,
            activation_id=activation.id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )

        assert result == ActivityActivationEndResult(activation=activation, transitioned=False)
