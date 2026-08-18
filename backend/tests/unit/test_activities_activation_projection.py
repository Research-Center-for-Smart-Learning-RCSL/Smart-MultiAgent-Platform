"""The activation read and the activation-started broadcast embed the
participant-safe type projection (Q-1, R30.26): a room reader gets `key` +
`payload_schema` without a round trip to the project-scoped endpoint, and
without `validator_config`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import activities
from contexts.activities.domain.errors import ActivityTypeNotFound
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivityType,
    ValidatorKind,
)
from contexts.activities.interfaces import broadcast

_NOW = dt.datetime(2026, 7, 28, tzinfo=dt.UTC)
_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def _make_type(**over: Any) -> ActivityType:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "key": "quiz",
        "name": "Quiz",
        "payload_schema": _SCHEMA,
        "validator_kind": ValidatorKind.IN_PROCESS,
        "validator_config": {"validator_id": "exact_match", "field": "answer", "expected": "day"},
        "retention_days": None,
        "version": 1,
        "created_at": _NOW,
        "deleted_at": None,
    }
    base.update(over)
    return ActivityType(**base)


def _make_activation(activity_type_id: uuid.UUID, **over: Any) -> ActivityActivation:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "chatroom_id": uuid.uuid4(),
        "activity_type_id": activity_type_id,
        "started_by_user_id": uuid.uuid4(),
        "status": ActivationStatus.ACTIVE,
        "created_at": _NOW,
    }
    base.update(over)
    return ActivityActivation(**base)


class _PublisherSpy:
    """Records every (channel, event, payload) across all constructions."""

    emitted: ClassVar[list[tuple[str, str, dict]]] = []

    def __init__(self, channel: str) -> None:
        self._channel = channel

    async def emit(self, event: str, payload: dict) -> None:
        _PublisherSpy.emitted.append((self._channel, event, payload))


async def test_active_activation_embeds_public_type(monkeypatch: pytest.MonkeyPatch) -> None:
    project_id = uuid.uuid4()
    activity_type = _make_type(project_id=project_id)
    activation = _make_activation(activity_type.id, chatroom_id=uuid.uuid4())
    facade = MagicMock()
    facade.get_active_activation = AsyncMock(return_value=activation)
    facade.resolve_type_for_project = AsyncMock(return_value=activity_type)
    monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
    monkeypatch.setattr(
        activities,
        "resolve_room_access",
        AsyncMock(return_value=SimpleNamespace(project_id=project_id)),
    )
    monkeypatch.setattr(activities, "ensure_can_read", MagicMock())

    out = await activities.get_active_activity_activation(
        chatroom_id=activation.chatroom_id,
        principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
        db=MagicMock(),
    )

    assert out is not None
    assert out.activity_type is not None
    assert out.activity_type.key == activity_type.key
    assert out.activity_type.payload_schema == activity_type.payload_schema
    assert "validator_config" not in out.activity_type.model_dump()


async def test_activation_started_broadcast_carries_public_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _PublisherSpy.emitted = []
    monkeypatch.setattr(broadcast, "Publisher", _PublisherSpy)
    activity_type = _make_type()
    activation = _make_activation(activity_type.id)

    await broadcast.dispatch_activation_started(activation, activity_type)

    assert len(_PublisherSpy.emitted) == 1
    _channel, event, payload = _PublisherSpy.emitted[0]
    assert event == "activity.activation.started"
    assert payload["activity_type"]["key"] == activity_type.key
    assert payload["activity_type"]["payload_schema"] == activity_type.payload_schema
    assert "validator_config" not in payload["activity_type"]


async def test_resolve_activation_type_degrades_to_none_on_facade_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient DB error resolving the embed must not fail the caller's
    already-committed activation change (code-review, post-commit best-effort)."""
    project_id = uuid.uuid4()
    activity_type = _make_type(project_id=project_id)
    activation = _make_activation(activity_type.id)
    facade = MagicMock()
    facade.resolve_type_for_project = AsyncMock(side_effect=RuntimeError("db hiccup"))

    result = await activities._resolve_activation_type(facade, project_id=project_id, activation=activation)

    assert result is None


async def test_resolve_activation_type_degrades_to_none_when_unreachable() -> None:
    """An unreachable type embeds as absent, not as a 500.

    Same arm as the transient-error case above, and deliberately so: the client's
    fallback room-scoped read recovers a missed embed, and that read applies the
    same gate — so degrading here cannot widen access.
    """
    activation = _make_activation(uuid.uuid4())
    facade = MagicMock()
    facade.resolve_type_for_project = AsyncMock(side_effect=ActivityTypeNotFound(str(uuid.uuid4())))

    result = await activities._resolve_activation_type(facade, project_id=uuid.uuid4(), activation=activation)

    assert result is None


async def test_activation_started_broadcast_survives_type_projection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole dispatch (including payload construction) is best-effort: it
    must never propagate and fail a request whose write already committed."""
    _PublisherSpy.emitted = []
    monkeypatch.setattr(broadcast, "Publisher", _PublisherSpy)
    monkeypatch.setattr(
        broadcast, "activity_type_public_payload", MagicMock(side_effect=RuntimeError("bad projection"))
    )
    activity_type = _make_type()
    activation = _make_activation(activity_type.id)

    await broadcast.dispatch_activation_started(activation, activity_type)

    assert _PublisherSpy.emitted == []
