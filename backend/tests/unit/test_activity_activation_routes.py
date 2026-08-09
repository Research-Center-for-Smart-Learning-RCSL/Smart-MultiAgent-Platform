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
