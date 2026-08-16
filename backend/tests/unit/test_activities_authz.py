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
from fastapi import HTTPException

from app.api.v1 import activities
from contexts.activities.application.example_service import PlatformExample
from contexts.activities.application.type_service import TypeRegistration
from contexts.activities.domain.errors import ActivityTypeNotFound
from contexts.activities.domain.models import ActivityType, ActivityTypeScope, ValidatorKind

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


class TestRegisterResponseRelaysTheCollisionWarning:
    """AC-1's route half. The service answers advisory, the route has to carry it:
    a warning nothing renders is the same as no warning ([R30.02])."""

    async def _register(self, monkeypatch: pytest.MonkeyPatch, *, shadowed: bool) -> Any:
        project_id = uuid.uuid4()
        created = _make_type(project_id=project_id, key="mandala-9grid")
        facade = MagicMock()
        facade.register_type = AsyncMock(
            return_value=TypeRegistration(activity_type=created, shadowed_by_platform=shadowed)
        )
        db = MagicMock()
        db.commit = AsyncMock()
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(activities, "assert_project_owner", AsyncMock())

        return await activities.register_activity_type(
            body=activities.ActivityTypeIn(
                key="mandala-9grid",
                name="Mandala",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "filled_count", "min_filled": 0},
            ),
            project_id=project_id,
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
            db=db,
        )

    async def test_it_carries_the_warning_alongside_the_created_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = await self._register(monkeypatch, shadowed=True)

        assert out.shadowed_by_platform is True
        # The row itself is unchanged: the response is a superset of what the
        # client always read, not a wrapper it has to unpack.
        assert out.key == "mandala-9grid"
        assert out.payload_schema == _SCHEMA
        assert out.scope is ActivityTypeScope.PROJECT
        assert out.validator_config  # the owner-confidential config is still returned

    async def test_the_ordinary_case_reports_no_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = await self._register(monkeypatch, shadowed=False)

        assert out.shadowed_by_platform is False


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


class TestPlatformExampleRoutes:
    """AC-4/AC-14: the Project Owner's opt-in surface ([R30.33])."""

    async def test_the_listing_is_owner_gated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not plain membership: the only thing this listing is for is deciding
        what to enable, which is the owner's call."""
        facade = MagicMock()
        facade.list_platform_examples_for_project = AsyncMock(return_value=())
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(
            activities, "assert_project_owner", AsyncMock(side_effect=HTTPException(status_code=403))
        )

        with pytest.raises(HTTPException) as exc:
            await activities.list_platform_activity_examples(
                project_id=uuid.uuid4(),
                principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
                db=MagicMock(),
            )

        assert exc.value.status_code == 403
        facade.list_platform_examples_for_project.assert_not_awaited()

    async def test_the_listing_carries_the_consent_fields_and_no_validator_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-14: enabling an example whose ``expose_payload_to_agent`` is true
        sends participant text to the project's LLM provider, so the flag has to
        reach the dialog. ``validator_config`` must not — it may hold answer keys
        ([R30.25])."""
        platform_type = _make_type(
            project_id=None,
            scope=ActivityTypeScope.PLATFORM,
            key="mandala-9grid",
            expose_payload_to_agent=True,
        )
        facade = MagicMock()
        facade.list_platform_examples_for_project = AsyncMock(
            return_value=(PlatformExample(activity_type=platform_type, enabled=False),)
        )
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(activities, "assert_project_owner", AsyncMock())

        out = await activities.list_platform_activity_examples(
            project_id=uuid.uuid4(),
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
            db=MagicMock(),
        )

        assert out[0].key == "mandala-9grid"
        assert out[0].expose_payload_to_agent is True
        assert out[0].enabled is False
        assert "validator_config" not in out[0].model_dump()
        assert "payload_schema" not in out[0].model_dump()

    async def test_opt_in_is_owner_gated_and_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        project_id, type_id = uuid.uuid4(), uuid.uuid4()
        facade = MagicMock()
        facade.opt_project_in = AsyncMock(return_value=False)
        db = MagicMock()
        db.commit = AsyncMock()
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(activities, "assert_project_owner", AsyncMock())

        out = await activities.opt_project_into_activity_type(
            body=activities.ActivityTypeOptInIn(activity_type_id=type_id),
            project_id=project_id,
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
            db=db,
        )

        assert facade.opt_project_in.await_args.kwargs["project_id"] == project_id
        assert facade.opt_project_in.await_args.kwargs["activity_type_id"] == type_id
        db.commit.assert_awaited_once()
        assert out.shadows_owned_key is False

    async def test_opt_in_relays_the_collision_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-2: the route is where the service's advisory answer becomes visible.
        It used to answer 204, which had nowhere to put it."""
        facade = MagicMock()
        facade.opt_project_in = AsyncMock(return_value=True)
        db = MagicMock()
        db.commit = AsyncMock()
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(activities, "assert_project_owner", AsyncMock())

        out = await activities.opt_project_into_activity_type(
            body=activities.ActivityTypeOptInIn(activity_type_id=uuid.uuid4()),
            project_id=uuid.uuid4(),
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
            db=db,
        )

        assert out.shadows_owned_key is True
        db.commit.assert_awaited_once()

    async def test_opt_out_commits_before_notifying_each_ended_room(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same ordering rule as the type delete: no room may be told its
        activation ended before that is durable."""
        room_a, room_b = uuid.uuid4(), uuid.uuid4()
        act_a, act_b = uuid.uuid4(), uuid.uuid4()
        facade = MagicMock()
        facade.opt_project_out = AsyncMock(return_value=[(room_a, act_a), (room_b, act_b)])
        db = MagicMock()
        db.commit = AsyncMock()
        dispatch = AsyncMock()
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(activities, "assert_project_owner", AsyncMock())
        monkeypatch.setattr(activities, "dispatch_activation_ended", dispatch)

        await activities.opt_project_out_of_activity_type(
            project_id=uuid.uuid4(),
            type_id=uuid.uuid4(),
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
            db=db,
        )

        db.commit.assert_awaited_once()
        assert [c.args for c in dispatch.await_args_list] == [(room_a, act_a), (room_b, act_b)]

    async def test_a_non_owner_cannot_opt_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = MagicMock()
        facade.opt_project_out = AsyncMock()
        db = MagicMock()
        db.commit = AsyncMock()
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(
            activities, "assert_project_owner", AsyncMock(side_effect=HTTPException(status_code=403))
        )

        with pytest.raises(HTTPException):
            await activities.opt_project_out_of_activity_type(
                project_id=uuid.uuid4(),
                type_id=uuid.uuid4(),
                ctx=SimpleNamespace(actor_ip=None, request_id=None),
                principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False),
                db=db,
            )

        facade.opt_project_out.assert_not_awaited()
        db.commit.assert_not_awaited()


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
