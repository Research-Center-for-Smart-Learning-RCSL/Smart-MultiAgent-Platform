"""Cascade-delete of an activity type (R30.23): ending every active activation
referencing the type and notifying each affected room.

DB is mocked (facade internals replaced): pins the orchestration ordering, the
tenant guard, and the per-room notification fan-out - no Postgres required.
Mirrors the mocked-repo style of ``test_activities_services.py``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1 import activities
from contexts.activities.domain.errors import ActivityTypeNotFound, PlatformActivityTypeReadOnly
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivityActivationEndResult,
    ActivityType,
    ActivityTypeScope,
    ValidatorKind,
)
from contexts.activities.interfaces.facade import ActivitiesFacade

_NOW = dt.datetime(2026, 7, 13, tzinfo=dt.UTC)


def _type(project_id: uuid.UUID, type_id: uuid.UUID) -> ActivityType:
    return ActivityType(
        id=type_id,
        project_id=project_id,
        key="quiz",
        name="Quiz",
        payload_schema={},
        validator_kind=ValidatorKind.WEBHOOK,
        validator_config={"url": "https://example.test/score"},
        retention_days=None,
        version=1,
        created_at=_NOW,
    )


def _platform_type(type_id: uuid.UUID) -> ActivityType:
    return ActivityType(
        id=type_id,
        project_id=None,
        scope=ActivityTypeScope.PLATFORM,
        key="mandala-9grid",
        name="Mandala",
        payload_schema={},
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={"validator_id": "filled_count", "min_filled": 1},
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


def _facade_with(active: list[ActivityActivation], existing: ActivityType | None) -> ActivitiesFacade:
    facade = ActivitiesFacade(MagicMock())
    facade._types = MagicMock()
    facade._types.get_type = AsyncMock(return_value=existing)
    facade._types.soft_delete = AsyncMock()
    facade._activation_repo = MagicMock()
    facade._activation_repo.list_active_for_type = AsyncMock(return_value=active)
    facade._sessions = MagicMock()
    facade._sessions.close_open_for_type = AsyncMock(return_value=0)
    facade._optin_repo = MagicMock()
    facade._optin_repo.remove_all_for_type = AsyncMock(return_value=[])
    facade._activation = MagicMock()

    async def _end(*, chatroom_id, activation_id, **_kw):  # type: ignore[no-untyped-def]
        match = next(a for a in active if a.id == activation_id)
        return ActivityActivationEndResult(activation=match, transitioned=True)

    facade._activation.end = AsyncMock(side_effect=_end)
    return facade


class TestDeleteTypeCascade:
    async def test_ends_every_active_activation_across_rooms_then_tombstones(self) -> None:
        project_id, type_id = uuid.uuid4(), uuid.uuid4()
        room_a, room_b = uuid.uuid4(), uuid.uuid4()
        act_a = _activation(room_a, type_id)
        act_b = _activation(room_b, type_id)
        facade = _facade_with([act_a, act_b], _type(project_id, type_id))

        ended = await facade.delete_type(
            project_id=project_id,
            type_id=type_id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )

        assert set(ended) == {(room_a, act_a.id), (room_b, act_b.id)}
        assert facade._activation.end.await_count == 2
        facade._sessions.close_open_for_type.assert_awaited_once_with(type_id)
        facade._types.soft_delete.assert_awaited_once()

    async def test_a_project_scoped_delete_revokes_no_optin(self) -> None:
        """AC-4: opt-ins exist only for platform types, so the shared cascade must
        not carry the removal — putting it there would issue a pointless
        cross-tenant DELETE on every project's own type delete."""
        project_id, type_id = uuid.uuid4(), uuid.uuid4()
        facade = _facade_with([_activation(uuid.uuid4(), type_id)], _type(project_id, type_id))

        await facade.delete_type(
            project_id=project_id,
            type_id=type_id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )

        facade._optin_repo.remove_all_for_type.assert_not_awaited()
        assert facade._types.soft_delete.await_args.kwargs.get("extra_metadata") is None

    async def test_non_transitioning_activation_is_not_reported(self) -> None:
        project_id, type_id = uuid.uuid4(), uuid.uuid4()
        room = uuid.uuid4()
        act = _activation(room, type_id)
        facade = _facade_with([act], _type(project_id, type_id))
        # Simulate a lost race: the row was ended between the list and the end.
        facade._activation.end = AsyncMock(
            return_value=ActivityActivationEndResult(activation=act, transitioned=False)
        )

        ended = await facade.delete_type(
            project_id=project_id,
            type_id=type_id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )

        assert ended == []
        facade._types.soft_delete.assert_awaited_once()  # still tombstones the type

    async def test_unknown_type_raises_and_touches_nothing(self) -> None:
        facade = _facade_with([], existing=None)

        with pytest.raises(ActivityTypeNotFound):
            await facade.delete_type(
                project_id=uuid.uuid4(),
                type_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        facade._activation_repo.list_active_for_type.assert_not_awaited()
        facade._types.soft_delete.assert_not_awaited()

    async def test_cross_project_type_is_refused_before_any_write(self) -> None:
        # Type belongs to a different project than the path project.
        type_id = uuid.uuid4()
        facade = _facade_with([], _type(uuid.uuid4(), type_id))

        with pytest.raises(ActivityTypeNotFound):
            await facade.delete_type(
                project_id=uuid.uuid4(),  # not the type's project
                type_id=type_id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        facade._activation_repo.list_active_for_type.assert_not_awaited()
        facade._types.soft_delete.assert_not_awaited()


class TestPlatformTypeDeleteAuthority:
    """AC-7/AC-10: who may delete a platform type, and how far the cascade goes.

    The refusal on the project path is not a nicety: the cascade below it is
    unbounded across rooms, which is only correct when the type is going away for
    everyone. Letting one project owner through would end every other tenant's
    running class.
    """

    async def test_a_project_owner_cannot_delete_a_platform_type(self) -> None:
        type_id = uuid.uuid4()
        facade = _facade_with([], _platform_type(type_id))

        with pytest.raises(PlatformActivityTypeReadOnly):
            await facade.delete_type(
                project_id=uuid.uuid4(),
                type_id=type_id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        facade._activation_repo.list_active_for_type.assert_not_awaited()
        facade._sessions.close_open_for_type.assert_not_awaited()
        facade._types.soft_delete.assert_not_awaited()

    async def test_an_admin_delete_ends_activations_in_every_tenant(self) -> None:
        """The admin path legitimately spans tenants: the type is going away."""
        type_id = uuid.uuid4()
        room_a, room_b = uuid.uuid4(), uuid.uuid4()
        act_a, act_b = _activation(room_a, type_id), _activation(room_b, type_id)
        facade = _facade_with([act_a, act_b], _platform_type(type_id))

        ended = await facade.delete_platform_type(
            type_id=type_id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )

        assert set(ended) == {(room_a, act_a.id), (room_b, act_b.id)}
        facade._sessions.close_open_for_type.assert_awaited_once_with(type_id)
        facade._types.soft_delete.assert_awaited_once()

    async def test_an_admin_delete_revokes_every_projects_optin(self) -> None:
        """AC-2: the type delete is soft, so the FK's ON DELETE CASCADE never
        fires — the opt-in rows have to be removed in code or they outlive the
        type they authorize forever."""
        type_id = uuid.uuid4()
        facade = _facade_with([], _platform_type(type_id))

        await facade.delete_platform_type(
            type_id=type_id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )

        facade._optin_repo.remove_all_for_type.assert_awaited_once_with(type_id)

    async def test_the_delete_event_records_how_many_optins_were_revoked(self) -> None:
        """AC-2: the blast radius rides on the operation that actually happened."""
        type_id = uuid.uuid4()
        facade = _facade_with([], _platform_type(type_id))
        facade._optin_repo.remove_all_for_type = AsyncMock(return_value=[uuid.uuid4(), uuid.uuid4()])

        await facade.delete_platform_type(
            type_id=type_id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )

        assert facade._types.soft_delete.await_args.kwargs["extra_metadata"] == {"optins_removed": "2"}

    async def test_the_admin_delete_refuses_a_project_scoped_type(self) -> None:
        """[R30.31]: the admin surface grants no delete over a project's own types."""
        project_id, type_id = uuid.uuid4(), uuid.uuid4()
        facade = _facade_with([], _type(project_id, type_id))

        with pytest.raises(ActivityTypeNotFound):
            await facade.delete_platform_type(
                type_id=type_id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        facade._activation_repo.list_active_for_type.assert_not_awaited()
        facade._types.soft_delete.assert_not_awaited()


class TestPlatformDeleteAuditRecord:
    """AC-2/AC-3: what the delete writes to the audit trail, and what it must not.

    The count rides on the ``activity_type.deleted`` event rather than N synthetic
    ``activity_type.opted_out`` rows. An admin delete is a different operation with
    a different actor: emitting an opt-out per project would put a statement in the
    append-only trail that no Project Owner ever made.
    """

    @staticmethod
    def _facade(type_id: uuid.UUID, revoked: list[uuid.UUID]) -> ActivitiesFacade:
        facade = ActivitiesFacade(MagicMock())
        # A real ActivityTypeService over a mocked repo, so the metadata dict the
        # service actually builds is what gets asserted.
        facade._types._repo = MagicMock()
        facade._types._repo.get = AsyncMock(return_value=_platform_type(type_id))
        facade._types._repo.soft_delete = AsyncMock(return_value=True)
        facade._activation_repo = MagicMock()
        facade._activation_repo.list_active_for_type = AsyncMock(return_value=[])
        facade._sessions = MagicMock()
        facade._sessions.close_open_for_type = AsyncMock(return_value=0)
        facade._optin_repo = MagicMock()
        facade._optin_repo.remove_all_for_type = AsyncMock(return_value=revoked)
        return facade

    async def test_the_deleted_event_carries_the_revoked_count(self) -> None:
        type_id = uuid.uuid4()
        facade = self._facade(type_id, [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])

        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()) as emit:
            await facade.delete_platform_type(
                type_id=type_id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        emit.assert_awaited_once()
        event = emit.await_args.args[1]
        assert event.action == "activity_type.deleted"
        assert event.metadata["optins_removed"] == "3"
        # The fields that were already there must survive the merge.
        assert event.metadata["scope"] == ActivityTypeScope.PLATFORM.value
        assert event.metadata["key"] == "mandala-9grid"

    async def test_no_opted_out_event_is_emitted_for_the_projects_it_revoked(self) -> None:
        """AC-3: one operation happened, so one row is written."""
        type_id = uuid.uuid4()
        facade = self._facade(type_id, [uuid.uuid4(), uuid.uuid4()])

        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()) as emit:
            await facade.delete_platform_type(
                type_id=type_id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        actions = [call.args[1].action for call in emit.await_args_list]
        assert actions == ["activity_type.deleted"]

    async def test_a_type_with_no_optins_records_a_zero(self) -> None:
        """Zero rather than an absent key: "nobody had enabled it" is a fact worth
        distinguishing from "this predates the count"."""
        type_id = uuid.uuid4()
        facade = self._facade(type_id, [])

        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()) as emit:
            await facade.delete_platform_type(
                type_id=type_id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        assert emit.await_args.args[1].metadata["optins_removed"] == "0"

    async def test_a_project_type_delete_writes_no_optin_count(self) -> None:
        """AC-4: the project path's audit record is unchanged."""
        project_id, type_id = uuid.uuid4(), uuid.uuid4()
        facade = self._facade(type_id, [])
        facade._types._repo.get = AsyncMock(return_value=_type(project_id, type_id))

        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()) as emit:
            await facade.delete_type(
                project_id=project_id,
                type_id=type_id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        assert "optins_removed" not in emit.await_args.args[1].metadata
        facade._optin_repo.remove_all_for_type.assert_not_awaited()


class TestDeleteRoute:
    async def test_owner_delete_commits_then_notifies_each_ended_room(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id, type_id = uuid.uuid4(), uuid.uuid4()
        room_a, room_b = uuid.uuid4(), uuid.uuid4()
        act_a, act_b = uuid.uuid4(), uuid.uuid4()
        facade = MagicMock()
        facade.delete_type = AsyncMock(return_value=[(room_a, act_a), (room_b, act_b)])
        db = MagicMock()
        db.commit = AsyncMock()
        dispatch = AsyncMock()

        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(activities, "assert_project_owner", AsyncMock())
        monkeypatch.setattr(activities, "dispatch_activation_ended", dispatch)

        # 204 No Content: the route returns None.
        await activities.delete_activity_type(
            project_id=project_id,
            type_id=type_id,
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
            principal=SimpleNamespace(user_id=uuid.uuid4()),
            db=db,
        )

        db.commit.assert_awaited_once()
        assert dispatch.await_args_list[0].args == (room_a, act_a)
        assert dispatch.await_args_list[1].args == (room_b, act_b)
        assert dispatch.await_count == 2

    async def test_non_owner_is_refused_before_any_delete(self, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = MagicMock()
        facade.delete_type = AsyncMock()
        db = MagicMock()
        db.commit = AsyncMock()

        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(
            activities,
            "assert_project_owner",
            AsyncMock(side_effect=HTTPException(status_code=403)),
        )
        monkeypatch.setattr(activities, "dispatch_activation_ended", AsyncMock())

        with pytest.raises(HTTPException) as exc:
            await activities.delete_activity_type(
                project_id=uuid.uuid4(),
                type_id=uuid.uuid4(),
                ctx=SimpleNamespace(actor_ip=None, request_id=None),
                principal=SimpleNamespace(user_id=uuid.uuid4()),
                db=db,
            )

        assert exc.value.status_code == 403
        facade.delete_type.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_unknown_type_propagates_and_does_not_commit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = MagicMock()
        facade.delete_type = AsyncMock(side_effect=ActivityTypeNotFound("nope"))
        db = MagicMock()
        db.commit = AsyncMock()
        dispatch = AsyncMock()

        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(activities, "assert_project_owner", AsyncMock())
        monkeypatch.setattr(activities, "dispatch_activation_ended", dispatch)

        with pytest.raises(ActivityTypeNotFound):
            await activities.delete_activity_type(
                project_id=uuid.uuid4(),
                type_id=uuid.uuid4(),
                ctx=SimpleNamespace(actor_ip=None, request_id=None),
                principal=SimpleNamespace(user_id=uuid.uuid4()),
                db=db,
            )

        db.commit.assert_not_awaited()
        dispatch.assert_not_awaited()
