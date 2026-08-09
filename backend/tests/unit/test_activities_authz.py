"""Room-scoped participant read for an activity type, and project-scoped
list redaction of ``validator_config`` for non-owners (R30.25/R30.26).

Mirrors the mocked-repo/mocked-facade style of ``test_activity_type_edit.py``
and ``test_activity_activation_routes.py`` — no Postgres required.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import activities
from contexts.activities.domain.errors import ActivityTypeNotFound
from contexts.activities.domain.models import ActivityType, ValidatorKind

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


class TestRoomScopedTypeRead:
    """GET /api/chatrooms/{chatroom_id}/activity-types/{type_id} (Q-1, AC-2/AC-3/AC-5)."""

    async def test_room_scoped_type_read_allows_guest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        project_id = uuid.uuid4()
        activity_type = _make_type(project_id=project_id)
        facade = MagicMock()
        facade.resolve_type_for_project = AsyncMock(return_value=activity_type)
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(
            activities,
            "resolve_room_access",
            AsyncMock(return_value=SimpleNamespace(project_id=project_id)),
        )
        monkeypatch.setattr(activities, "ensure_can_read", MagicMock())

        out = await activities.get_room_activity_type(
            chatroom_id=uuid.uuid4(),
            type_id=activity_type.id,
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
            db=MagicMock(),
        )

        assert out.id == activity_type.id
        assert out.key == activity_type.key
        assert out.payload_schema == activity_type.payload_schema

    async def test_room_scoped_type_read_omits_validator_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id = uuid.uuid4()
        activity_type = _make_type(
            project_id=project_id,
            validator_config={"validator_id": "exact_match", "field": "answer", "expected": "day"},
        )
        facade = MagicMock()
        facade.resolve_type_for_project = AsyncMock(return_value=activity_type)
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(
            activities,
            "resolve_room_access",
            AsyncMock(return_value=SimpleNamespace(project_id=project_id)),
        )
        monkeypatch.setattr(activities, "ensure_can_read", MagicMock())

        out = await activities.get_room_activity_type(
            chatroom_id=uuid.uuid4(),
            type_id=activity_type.id,
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
            db=MagicMock(),
        )

        assert "validator_config" not in out.model_dump()
        assert "validator_kind" not in out.model_dump()

    async def test_room_scoped_type_read_gates_on_the_rooms_project(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The route must ask the reachability resolver with the *room's* project
        and let its refusal through untranslated.

        Since 0076 the route no longer compares ``project_id`` itself — a platform
        type has none. What is pinned here is that the room's project is what
        bounds the read and that the 404 is not swallowed; which types that
        resolver admits is pinned in ``test_platform_type_reachability.py``.
        """
        room_project_id = uuid.uuid4()
        type_id = uuid.uuid4()
        facade = MagicMock()
        facade.resolve_type_for_project = AsyncMock(side_effect=ActivityTypeNotFound(str(type_id)))
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(
            activities,
            "resolve_room_access",
            AsyncMock(return_value=SimpleNamespace(project_id=room_project_id)),
        )
        monkeypatch.setattr(activities, "ensure_can_read", MagicMock())

        with pytest.raises(ActivityTypeNotFound):
            await activities.get_room_activity_type(
                chatroom_id=uuid.uuid4(),
                type_id=type_id,
                principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
                db=MagicMock(),
            )

        facade.resolve_type_for_project.assert_awaited_once_with(
            project_id=room_project_id, activity_type_id=type_id
        )


class TestListTypesRedaction:
    """GET /api/projects/{project_id}/activity-types (Q-2, AC-4)."""

    async def test_list_types_redacts_validator_config_for_non_owner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id = uuid.uuid4()
        activity_type = _make_type(project_id=project_id)
        facade = MagicMock()
        facade.list_types = AsyncMock(return_value=[activity_type])
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(activities, "assert_project_membership", AsyncMock())
        monkeypatch.setattr(activities, "is_project_owner_or_admin", AsyncMock(return_value=False))

        out = await activities.list_activity_types(
            project_id=project_id,
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
            db=MagicMock(),
        )

        assert out[0].validator_config == {}

    async def test_list_types_keeps_validator_config_for_owner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        project_id = uuid.uuid4()
        activity_type = _make_type(project_id=project_id)
        facade = MagicMock()
        facade.list_types = AsyncMock(return_value=[activity_type])
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(activities, "assert_project_membership", AsyncMock())
        monkeypatch.setattr(activities, "is_project_owner_or_admin", AsyncMock(return_value=True))

        out = await activities.list_activity_types(
            project_id=project_id,
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
            db=MagicMock(),
        )

        assert out[0].validator_config == activity_type.validator_config
