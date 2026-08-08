"""`/api/admin/activity-*` — the cross-project governance read ([R30.31]).

The route's own hydration logic is what these tests exercise: only the facade
*methods* are patched, so the batching, the dict lookups, and the response
mapping all run for real. Patching `ActivitiesFacade` wholesale would leave the
part worth testing unexecuted — the failure mode that let a broken seeder ship
green earlier in this codebase.
"""

from __future__ import annotations

import datetime as dt
import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1 import admin_activities
from app.api.v1.admin_deps import require_admin
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivityType,
    ValidatorKind,
)
from contexts.activities.interfaces.facade import ActivitiesFacade
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.tenancy.interfaces.facade import TenancyFacade

_NOW = dt.datetime(2026, 8, 8, tzinfo=dt.UTC)
_ADMIN = SimpleNamespace(user_id=uuid.uuid4(), is_admin=True)


def _make_type(*, project_id: uuid.UUID, key: str = "k", **over: object) -> ActivityType:
    base: dict = {
        "id": uuid.uuid4(),
        "project_id": project_id,
        "key": key,
        "name": f"name-{key}",
        "payload_schema": {"type": "object", "properties": {"a": {"type": "string"}}},
        "validator_kind": ValidatorKind.IN_PROCESS,
        "validator_config": {"validator_id": "filled_count", "min_filled": 2},
        "retention_days": None,
        "version": 1,
        "created_at": _NOW,
        "deleted_at": None,
    }
    base.update(over)
    return ActivityType(**base)


def _make_activation(*, chatroom_id: uuid.UUID, activity_type_id: uuid.UUID) -> ActivityActivation:
    return ActivityActivation(
        id=uuid.uuid4(),
        chatroom_id=chatroom_id,
        activity_type_id=activity_type_id,
        started_by_user_id=uuid.uuid4(),
        status=ActivationStatus.ACTIVE,
        created_at=_NOW,
    )


class TestAdminGate:
    """`require_admin` is the only access control on this surface, so it is worth
    pinning both that it rejects and that the routes are actually wired to it."""

    async def test_require_admin_rejects_a_non_admin(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await require_admin(principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False))
        assert exc.value.status_code == 403

    async def test_require_admin_admits_an_admin(self) -> None:
        assert await require_admin(principal=_ADMIN) is _ADMIN

    @pytest.mark.parametrize(
        "route",
        [
            admin_activities.list_all_activity_types,
            admin_activities.list_all_active_activations,
        ],
        ids=["types", "activations"],
    )
    def test_route_depends_on_the_shared_require_admin(self, route: object) -> None:
        # Not a local re-implementation (admin_ip_bans has one) and not omitted.
        params = inspect.signature(route).parameters  # type: ignore[arg-type]
        deps = [p.default.dependency for p in params.values() if hasattr(p.default, "dependency")]
        assert require_admin in deps


class TestListAllActivityTypes:
    async def test_returns_types_from_several_projects_hydrated_with_project_names(self) -> None:
        p1, p2 = uuid.uuid4(), uuid.uuid4()
        types = [
            _make_type(project_id=p1, key="alpha"),
            _make_type(project_id=p2, key="beta"),
            _make_type(project_id=p1, key="gamma"),
        ]
        projects = {
            p1: SimpleNamespace(id=p1, name="Project One"),
            p2: SimpleNamespace(id=p2, name="Project Two"),
        }

        with (
            patch.object(ActivitiesFacade, "list_all_types", AsyncMock(return_value=types)),
            patch.object(TenancyFacade, "get_projects", AsyncMock(return_value=projects)) as gp,
        ):
            out = await admin_activities.list_all_activity_types(
                cursor=None, limit=50, _=_ADMIN, db=MagicMock()
            )

        assert [r.key for r in out] == ["alpha", "beta", "gamma"]
        assert [r.project_name for r in out] == ["Project One", "Project Two", "Project One"]
        # AC-1: more than one project in a single response.
        assert len({r.project_id for r in out}) == 2

        # AC-2: one batch lookup for the whole page, and every project on the page
        # is in it — three rows must not become three queries.
        assert gp.await_count == 1
        assert set(gp.await_args.args[0]) == {p1, p2}

    async def test_carries_the_governance_fields_and_validator_config(self) -> None:
        p = uuid.uuid4()
        at = _make_type(
            project_id=p,
            key="k",
            expose_payload_to_agent=False,
            echo_includes_content=True,
            retention_days=365,
        )
        with (
            patch.object(ActivitiesFacade, "list_all_types", AsyncMock(return_value=[at])),
            patch.object(TenancyFacade, "get_projects", AsyncMock(return_value={})),
        ):
            out = await admin_activities.list_all_activity_types(
                cursor=None, limit=50, _=_ADMIN, db=MagicMock()
            )

        assert out[0].expose_payload_to_agent is False
        assert out[0].echo_includes_content is True
        assert out[0].retention_days == 365
        # Q-3: shown deliberately, so a regression that drops it should fail here.
        assert out[0].validator_config == {"validator_id": "filled_count", "min_filled": 2}
        assert out[0].validator_kind == "in_process"

    async def test_missing_project_degrades_to_a_null_name_rather_than_raising(self) -> None:
        """A soft-deleted or vanished project must not 500 the whole admin page."""
        with (
            patch.object(
                ActivitiesFacade,
                "list_all_types",
                AsyncMock(return_value=[_make_type(project_id=uuid.uuid4())]),
            ),
            patch.object(TenancyFacade, "get_projects", AsyncMock(return_value={})),
        ):
            out = await admin_activities.list_all_activity_types(
                cursor=None, limit=50, _=_ADMIN, db=MagicMock()
            )

        assert out[0].project_name is None

    async def test_cursor_and_limit_reach_the_facade(self) -> None:
        cursor = uuid.uuid4()
        with (
            patch.object(ActivitiesFacade, "list_all_types", AsyncMock(return_value=[])) as lt,
            patch.object(TenancyFacade, "get_projects", AsyncMock(return_value={})),
        ):
            await admin_activities.list_all_activity_types(cursor=cursor, limit=7, _=_ADMIN, db=MagicMock())

        assert lt.await_args.kwargs == {"cursor": cursor, "limit": 7}


class TestListAllActiveActivations:
    async def test_hydrates_room_and_type_names_with_one_batch_lookup_each(self) -> None:
        room1, room2 = uuid.uuid4(), uuid.uuid4()
        t1 = _make_type(project_id=uuid.uuid4(), key="mandala-9grid")
        activations = [
            _make_activation(chatroom_id=room1, activity_type_id=t1.id),
            _make_activation(chatroom_id=room2, activity_type_id=t1.id),
        ]
        rooms = {
            room1: SimpleNamespace(id=room1, name="Class A"),
            room2: SimpleNamespace(id=room2, name="Class B"),
        }

        with (
            patch.object(
                ActivitiesFacade, "list_all_active_activations", AsyncMock(return_value=activations)
            ),
            patch.object(ActivitiesFacade, "get_types_by_ids", AsyncMock(return_value={t1.id: t1})) as gt,
            patch.object(ConversationFacade, "get_chatrooms", AsyncMock(return_value=rooms)) as gc,
        ):
            out = await admin_activities.list_all_active_activations(
                cursor=None, limit=50, _=_ADMIN, db=MagicMock()
            )

        assert [r.chatroom_name for r in out] == ["Class A", "Class B"]
        assert [r.activity_type_key for r in out] == ["mandala-9grid", "mandala-9grid"]
        assert [r.activity_type_name for r in out] == ["name-mandala-9grid"] * 2

        # AC-2 / AC-4: one batch call per context, never per row, and the room name
        # comes through the conversation facade rather than a join.
        assert gt.await_count == 1
        assert gc.await_count == 1
        assert set(gc.await_args.args[0]) == {room1, room2}
        # The duplicate type id is passed once per row; what matters is one call.
        assert set(gt.await_args.args[0]) == {t1.id}

    async def test_missing_room_or_type_degrades_to_null_names(self) -> None:
        a = _make_activation(chatroom_id=uuid.uuid4(), activity_type_id=uuid.uuid4())
        with (
            patch.object(ActivitiesFacade, "list_all_active_activations", AsyncMock(return_value=[a])),
            patch.object(ActivitiesFacade, "get_types_by_ids", AsyncMock(return_value={})),
            patch.object(ConversationFacade, "get_chatrooms", AsyncMock(return_value={})),
        ):
            out = await admin_activities.list_all_active_activations(
                cursor=None, limit=50, _=_ADMIN, db=MagicMock()
            )

        assert out[0].chatroom_name is None
        assert out[0].activity_type_key is None
        assert out[0].activity_type_name is None
        # The ids themselves are always present, so a row is never useless.
        assert out[0].chatroom_id == a.chatroom_id
        assert out[0].activity_type_id == a.activity_type_id

    async def test_empty_page_issues_no_hydration_queries(self) -> None:
        with (
            patch.object(ActivitiesFacade, "list_all_active_activations", AsyncMock(return_value=[])),
            patch.object(ActivitiesFacade, "get_types_by_ids", AsyncMock(return_value={})) as gt,
            patch.object(ConversationFacade, "get_chatrooms", AsyncMock(return_value={})) as gc,
        ):
            out = await admin_activities.list_all_active_activations(
                cursor=None, limit=50, _=_ADMIN, db=MagicMock()
            )

        assert out == []
        # Called with an empty list; the repositories short-circuit without SQL.
        assert gt.await_args.args[0] == []
        assert gc.await_args.args[0] == []


class TestRouterRegistration:
    def test_router_is_mounted_under_the_admin_aggregate(self) -> None:
        """A router that exists but is never included is invisible in production.

        Asserted through `openapi()` rather than `app.routes`: FastAPI 0.137 defers
        a nested `include_router` as an unresolved wrapper, so the route list stays
        empty until something forces resolution. Reading the schema also checks the
        surface the generated frontend client is built from.
        """
        from fastapi import FastAPI

        from app.api.v1 import admin

        app = FastAPI()
        app.include_router(admin.router)

        paths = app.openapi()["paths"]
        assert "/api/admin/activity-types" in paths
        assert "/api/admin/activity-activations" in paths
        assert "get" in paths["/api/admin/activity-types"]
        assert "get" in paths["/api/admin/activity-activations"]
