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
from contexts.activities.application.example_service import (
    CatalogueEntry,
    CatalogueTypeEntry,
    InstallReport,
)
from contexts.activities.domain.errors import ActivityPolicyVersionMismatch, ExampleCourseNotFound
from contexts.activities.domain.models import (
    PLATFORM_SCOPE,
    ActivationStatus,
    ActivityActivation,
    ActivityPolicy,
    ActivityType,
    ActivityTypeScope,
    PolicyImpact,
    ValidatorKind,
)
from contexts.activities.interfaces.error_mapping import _MAP as _ACTIVITIES_MAP
from contexts.activities.interfaces.facade import ActivitiesFacade
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel.errors.context_handler import resolve_spec

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


_CTX = SimpleNamespace(actor_ip=None, request_id=None)


def _committing_db() -> MagicMock:
    db = MagicMock()
    db.commit = AsyncMock()
    return db


def _policy(**over: object) -> ActivityPolicy:
    base: dict = {
        "id": uuid.uuid4(),
        "scope": PLATFORM_SCOPE,
        "expose_payload_to_agent_default": True,
        "expose_payload_to_agent_locked": False,
        "echo_includes_content_default": False,
        "echo_includes_content_locked": False,
        "retention_days_default": None,
        "retention_days_max": None,
        "version": 1,
        "updated_at": None,
        "updated_by_user_id": None,
    }
    base.update(over)
    return ActivityPolicy(**base)


def _policy_in(**over: object) -> admin_activities.AdminActivityPolicyIn:
    base: dict = {
        "expose_payload_to_agent_default": True,
        "expose_payload_to_agent_locked": False,
        "echo_includes_content_default": False,
        "echo_includes_content_locked": False,
        "retention_days_default": None,
        "retention_days_max": None,
    }
    base.update(over)
    return admin_activities.AdminActivityPolicyIn(**base)


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
            admin_activities.list_platform_activity_types,
        ],
        ids=["types", "activations", "platform-types"],
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


class TestPolicyRoutes:
    """AC-12/AC-13 at the HTTP boundary: the If-Match handling and the gate."""

    async def test_get_returns_the_effective_policy(self) -> None:
        policy = _policy(version=0)
        with patch.object(ActivitiesFacade, "get_activity_policy", AsyncMock(return_value=policy)):
            out = await admin_activities.get_activity_policy(_=_ADMIN, db=MagicMock())

        # version 0 tells the client it is creating, not replacing.
        assert out.version == 0
        assert out.updated_at is None

    async def test_put_without_if_match_passes_no_expected_version(self) -> None:
        with patch.object(
            ActivitiesFacade, "update_activity_policy", AsyncMock(return_value=_policy(version=1))
        ) as upd:
            await admin_activities.put_activity_policy(
                body=_policy_in(), admin=_ADMIN, ctx=_CTX, db=_committing_db(), if_match=None
            )

        assert upd.await_args.kwargs["expected_version"] is None

    @pytest.mark.parametrize("header", ["7", '"7"', " 7 "], ids=["bare", "quoted", "padded"])
    async def test_put_parses_the_if_match_version(self, header: str) -> None:
        with patch.object(
            ActivitiesFacade, "update_activity_policy", AsyncMock(return_value=_policy(version=8))
        ) as upd:
            await admin_activities.put_activity_policy(
                body=_policy_in(), admin=_ADMIN, ctx=_CTX, db=_committing_db(), if_match=header
            )

        assert upd.await_args.kwargs["expected_version"] == 7

    async def test_an_unparseable_if_match_is_a_mismatch_not_a_missing_precondition(self) -> None:
        """Ignoring a malformed precondition would silently disable the guard."""
        with (
            patch.object(ActivitiesFacade, "update_activity_policy", AsyncMock()) as upd,
            pytest.raises(ActivityPolicyVersionMismatch),
        ):
            await admin_activities.put_activity_policy(
                body=_policy_in(), admin=_ADMIN, ctx=_CTX, db=_committing_db(), if_match="not-a-version"
            )

        upd.assert_not_awaited()

    async def test_impact_preview_reports_violations_without_saving(self) -> None:
        with (
            patch.object(
                ActivitiesFacade,
                "preview_policy_impact",
                AsyncMock(
                    return_value=PolicyImpact(violating_types=3, violating_activations=1, approximate=False)
                ),
            ) as prev,
            patch.object(ActivitiesFacade, "update_activity_policy", AsyncMock()) as upd,
        ):
            out = await admin_activities.preview_activity_policy_impact(
                body=_policy_in(expose_payload_to_agent_locked=True), _=_ADMIN, db=MagicMock()
            )

        assert out.violating_types == 3
        # Activities running right now are reported separately: enforcement is at
        # authoring and activation start, so a tightening does not stop them.
        assert out.violating_activations == 1
        assert out.approximate is False
        # A preview must not write.
        upd.assert_not_awaited()
        # The candidate policy, not the stored one, is what gets measured.
        assert prev.await_args.args[0].expose_payload_to_agent_locked is True

    @pytest.mark.parametrize(
        "route",
        [
            admin_activities.get_activity_policy,
            admin_activities.put_activity_policy,
            admin_activities.preview_activity_policy_impact,
        ],
        ids=["get", "put", "impact"],
    )
    def test_policy_routes_are_admin_gated(self, route: object) -> None:
        """AC-13. The policy can force agent exposure platform-wide."""
        params = inspect.signature(route).parameters  # type: ignore[arg-type]
        deps = [p.default.dependency for p in params.values() if hasattr(p.default, "dependency")]
        assert require_admin in deps


class TestExampleCatalogueRoutes:
    """AC-3/AC-8: the admin install and edit surface ([R30.32], [R30.23])."""

    async def test_the_catalogue_listing_carries_install_state_and_consent_fields(self) -> None:
        entry = CatalogueEntry(
            course_key="creative-thinking",
            title="Creative thinking",
            source="a school",
            activity_types=(
                CatalogueTypeEntry(
                    key="mandala-9grid",
                    name="Mandala",
                    expose_payload_to_agent=True,
                    echo_includes_content=False,
                    retention_days=None,
                    installed_type_id=None,
                ),
            ),
        )

        with patch.object(ActivitiesFacade, "list_example_catalogue", new=AsyncMock(return_value=(entry,))):
            out = await admin_activities.list_activity_examples(_=_ADMIN, db=MagicMock())

        assert [e.course_key for e in out] == ["creative-thinking"]
        assert out[0].fully_installed is False
        assert out[0].activity_types[0].expose_payload_to_agent is True
        assert out[0].activity_types[0].installed_type_id is None

    async def test_install_commits_and_reports_both_lists(self) -> None:
        db = _committing_db()
        report = InstallReport(
            course_key="creative-thinking",
            created=("mandala-9grid",),
            already_present=("six-hats-emotion-desk",),
        )

        with patch.object(
            ActivitiesFacade, "install_example_course", new=AsyncMock(return_value=report)
        ) as install:
            out = await admin_activities.install_activity_example(
                course_key="creative-thinking", admin=_ADMIN, ctx=_CTX, db=db
            )

        assert install.await_args.kwargs["course_key"] == "creative-thinking"
        assert install.await_args.kwargs["actor_user_id"] == _ADMIN.user_id
        db.commit.assert_awaited_once()
        assert out.created == ["mandala-9grid"]
        assert out.already_present == ["six-hats-emotion-desk"]

    async def test_an_unknown_course_key_maps_to_404_and_commits_nothing(self) -> None:
        """F-6/AC-1. The route adds no guard of its own, so what makes an admin's
        typo a 404 instead of a logged 500 is that the service raises a domain
        error this context's `_MAP` carries."""
        db = _committing_db()

        with (
            patch.object(
                ActivitiesFacade,
                "install_example_course",
                new=AsyncMock(side_effect=ExampleCourseNotFound("'nope' is not a shipped course")),
            ),
            pytest.raises(ExampleCourseNotFound),
        ):
            await admin_activities.install_activity_example(course_key="nope", admin=_ADMIN, ctx=_CTX, db=db)

        db.commit.assert_not_awaited()

        slug, status, _title = resolve_spec(ExampleCourseNotFound("nope"), _ACTIVITIES_MAP)
        assert (slug, status) == ("activities/example-course-not-found", 404)

    async def test_the_platform_edit_passes_only_the_four_permitted_fields(self) -> None:
        """AC-8: `key`, `payload_schema` and `validator_config` are not parameters
        of this route at all, so there is nothing for a client to send."""
        db = _committing_db()
        edited = _make_type(project_id=None, key="mandala-9grid", scope=ActivityTypeScope.PLATFORM)
        body = admin_activities.AdminPlatformActivityTypeIn(
            name="Mandala (no agent exposure)",
            retention_days=30,
            expose_payload_to_agent=False,
            echo_includes_content=True,
        )

        with patch.object(
            ActivitiesFacade, "update_platform_type", new=AsyncMock(return_value=edited)
        ) as update:
            out = await admin_activities.update_platform_activity_type(
                body=body, type_id=edited.id, admin=_ADMIN, ctx=_CTX, db=db
            )

        assert set(body.model_fields) == {
            "name",
            "retention_days",
            "expose_payload_to_agent",
            "echo_includes_content",
        }
        passed = update.await_args.kwargs
        assert passed["name"] == "Mandala (no agent exposure)"
        assert passed["retention_days"] == 30
        assert passed["expose_payload_to_agent"] is False
        assert passed["echo_includes_content"] is True
        db.commit.assert_awaited_once()
        # A platform row has no owning project, so the hydrated name is absent
        # rather than a lookup that has no answer.
        assert out.project_id is None
        assert out.project_name is None

    async def test_the_platform_delete_commits_before_broadcasting_each_room(self) -> None:
        db = _committing_db()
        room_a, room_b = uuid.uuid4(), uuid.uuid4()
        act_a, act_b = uuid.uuid4(), uuid.uuid4()
        dispatch = AsyncMock()

        with (
            patch.object(
                ActivitiesFacade,
                "delete_platform_type",
                new=AsyncMock(return_value=[(room_a, act_a), (room_b, act_b)]),
            ),
            patch.object(admin_activities, "dispatch_activation_ended", new=dispatch),
        ):
            await admin_activities.delete_platform_activity_type(
                type_id=uuid.uuid4(), admin=_ADMIN, ctx=_CTX, db=db
            )

        db.commit.assert_awaited_once()
        assert [c.args for c in dispatch.await_args_list] == [(room_a, act_a), (room_b, act_b)]


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
        assert {"get", "put"} <= set(paths["/api/admin/activity-policy"])
        assert "post" in paths["/api/admin/activity-policy/impact"]
        assert "get" in paths["/api/admin/activity-examples"]
        assert "post" in paths["/api/admin/activity-examples/{course_key}/install"]
        assert {"patch", "delete"} <= set(paths["/api/admin/activity-types/{type_id}"])
