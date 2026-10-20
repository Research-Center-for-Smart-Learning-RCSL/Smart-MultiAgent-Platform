"""Dashboard room and export gates against real tenancy and room-access rows."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.api.ws.project as ws_project_mod
from app.api.v1.dashboard import dashboard_summary, dashboard_timeseries, dashboard_watchlist
from app.api.v1.research_export import create_research_export
from contexts.activities.domain.models import ValidatorKind
from contexts.activities.infrastructure.repositories.activation_repo import ActivationRepository
from contexts.activities.infrastructure.repositories.session_repo import ActivitySessionRepository
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from contexts.conversation.domain.models import SenderType
from contexts.conversation.infrastructure import tables as conversation_t
from contexts.conversation.infrastructure.repositories.chatroom_repo import ChatroomRepository
from contexts.conversation.infrastructure.repositories.workspace_repo import WorkspaceRepository
from contexts.dashboard.domain.models import (
    DashboardMessageCounts,
    DashboardRoom,
    DashboardSummary,
    DashboardThresholds,
    DashboardTimeseriesBucket,
    DashboardWatchlist,
    DashboardWatchlistEntry,
    TrafficLight,
)
from contexts.dashboard.interfaces.facade import DashboardFacade
from contexts.identity.infrastructure.repositories import UserRepository
from contexts.identity.infrastructure.tables import users as users_t
from contexts.tenancy.domain.models import OrgMemberRole, ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)
from contexts.tenancy.infrastructure.tables import member_groups as member_groups_t
from contexts.tenancy.infrastructure.tables import orgs as orgs_t
from contexts.tenancy.infrastructure.tables import project_members as project_members_t
from contexts.tenancy.infrastructure.tables import projects as projects_t
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.permissions import Principal
from shared_kernel.realtime import AccessOutcome
from shared_kernel.realtime.ws_auth import WsAuth

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class _RoomScenario:
    project_id: uuid.UUID
    workspace_id: uuid.UUID
    owner_id: uuid.UUID
    member_id: uuid.UUID
    visible_room_id: uuid.UUID
    owners_only_room_id: uuid.UUID


async def _room_scenario(db: AsyncSession) -> _RoomScenario:
    u = uuid.uuid4().hex[:10]
    users = UserRepository(db)
    owner = (await users.insert(email=f"td-owner-{u}@test.invalid", password_hash="x" * 16)).id
    member = (await users.insert(email=f"td-member-{u}@test.invalid", password_hash="x" * 16)).id
    project = await ProjectRepository(db).create(
        name=f"td-project-{u}",
        owner_user_id=owner,
        owner_org_id=None,
        created_by_user_id=owner,
    )
    await ProjectMemberRepository(db).add(
        project_id=project.id,
        user_id=owner,
        role=ProjectMemberRole.OWNER,
    )
    await ProjectMemberRepository(db).add(
        project_id=project.id,
        user_id=member,
        role=ProjectMemberRole.MEMBER,
    )
    workspace = await WorkspaceRepository(db).create(project_id=project.id, name=f"td-workspace-{u}")
    visible = await ChatroomRepository(db).create(workspace_id=workspace.id, name="Members room")
    owners_only = await ChatroomRepository(db).create(
        workspace_id=workspace.id,
        name="Owners room",
        allow_project_members=False,
        allow_project_owners_only=True,
    )
    return _RoomScenario(project.id, workspace.id, owner, member, visible.id, owners_only.id)


async def _cleanup_project_and_users(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    user_ids: list[uuid.UUID],
) -> None:
    async with sessionmaker() as cleanup:
        await cleanup.execute(projects_t.delete().where(projects_t.c.id == project_id))
        for user_id in user_ids:
            await cleanup.execute(users_t.delete().where(users_t.c.id == user_id))
        await cleanup.commit()


def _principal(user_id: uuid.UUID) -> Principal:
    return Principal(user_id=user_id, is_admin=False, email_verified=True)


def _ws_auth(user_id: uuid.UUID) -> WsAuth:
    return WsAuth(
        principal=_principal(user_id),
        subprotocol="ticket.dashboard",
        access_token="test-token",
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=15),
        jti=uuid.uuid4(),
    )


class _FakeWebSocket:
    def __init__(self) -> None:
        self.accepted: str | None = None
        self.closed: tuple[int, str | None] | None = None

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = subprotocol

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason)


@pytest.mark.asyncio
async def test_dashboard_routes_filter_rooms_by_the_real_room_gate(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as db:
        scenario = await _room_scenario(db)
        await db.commit()

        async def summary(_facade, room_map):
            return DashboardSummary(
                rooms=[
                    DashboardRoom(
                        room_id=room_id,
                        room_name=room_name,
                        round=None,
                        total_submissions=0,
                        valid_count=0,
                        last_valid_submission_at=None,
                        status=TrafficLight.INACTIVE,
                        online_count=0,
                        participant_count=0,
                        message_counts=DashboardMessageCounts(human=0, agent=0),
                    )
                    for room_id, room_name in room_map.items()
                ],
                thresholds=DashboardThresholds(green_minutes=5, yellow_minutes=15, idle_minutes=10),
                generated_at=dt.datetime.now(dt.UTC),
            )

        async def timeseries(_facade, room_map, **_kwargs):
            return [
                DashboardTimeseriesBucket(
                    room_id=room_id,
                    bucket=dt.datetime(2026, 10, 20, tzinfo=dt.UTC),
                    count=3,
                )
                for room_id in room_map
            ]

        async def watchlist(_facade, room_map, *, project_id):
            return DashboardWatchlist(
                entries=[
                    DashboardWatchlistEntry(
                        room_id=room_id,
                        kind="user",
                        display_name=None,
                        subject_code=f"u:{str(room_id)[:8]}",
                        valid_count=3,
                        last_valid_submission_at=None,
                        needs_attention=False,
                        reason=None,
                    )
                    for room_id in room_map
                ]
            )

        try:
            with (
                patch.object(DashboardFacade, "summary", summary),
                patch.object(DashboardFacade, "timeseries", timeseries),
                patch.object(DashboardFacade, "watchlist", watchlist),
            ):
                for user_id, expected in (
                    (scenario.member_id, {scenario.visible_room_id}),
                    (scenario.owner_id, {scenario.visible_room_id, scenario.owners_only_room_id}),
                ):
                    summary = await dashboard_summary(
                        workspace_id=scenario.workspace_id,
                        principal=_principal(user_id),
                        db=db,
                    )
                    ts = await dashboard_timeseries(
                        workspace_id=scenario.workspace_id,
                        principal=_principal(user_id),
                        db=db,
                    )
                    roster = await dashboard_watchlist(
                        workspace_id=scenario.workspace_id,
                        principal=_principal(user_id),
                        db=db,
                    )
                    assert {room.room_id for room in summary.rooms} == expected
                    assert {bucket.room_id for bucket in ts.buckets} == expected
                    assert {entry.room_id for entry in roster.entries} == expected

                await db.execute(
                    update(projects_t)
                    .where(projects_t.c.id == scenario.project_id)
                    .values(deleted_at=dt.datetime.now(dt.UTC))
                )
                await db.commit()
                with pytest.raises(HTTPException) as deleted:
                    await dashboard_summary(
                        workspace_id=scenario.workspace_id,
                        principal=_principal(scenario.owner_id),
                        db=db,
                    )
                assert deleted.value.status_code == 404
        finally:
            await _cleanup_project_and_users(
                sessionmaker,
                scenario.project_id,
                [scenario.owner_id, scenario.member_id],
            )


@pytest.mark.asyncio
async def test_watchlist_excludes_project_owner_and_names_recent_guest(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as db:
        scenario = await _room_scenario(db)
        guest_id = uuid.uuid4()
        group_id = uuid.uuid4()
        type_id = await ActivityTypeRepository(db).create(
            project_id=scenario.project_id,
            key=f"dashboard-{uuid.uuid4().hex[:10]}",
            name="Dashboard test",
            payload_schema={},
            validator_kind=ValidatorKind.IN_PROCESS,
            validator_config={"validator_id": "filled_count", "min_filled": 1},
            retention_days=None,
            expose_payload_to_agent=True,
            echo_includes_content=False,
        )
        activation_id = await ActivationRepository(db).create_active(
            chatroom_id=scenario.visible_room_id,
            activity_type_id=type_id,
            started_by_user_id=scenario.owner_id,
        )
        assert activation_id is not None
        await db.execute(
            member_groups_t.insert().values(
                id=group_id,
                project_id=scenario.project_id,
                name="Team North",
                created_by_user_id=scenario.owner_id,
            )
        )
        await ActivitySessionRepository(db).create_open_for_group(
            activity_type_id=type_id,
            chatroom_id=scenario.visible_room_id,
            member_group_id=group_id,
            activation_id=activation_id,
        )
        activation = await ActivationRepository(db).get(activation_id)
        assert activation is not None
        await db.execute(
            conversation_t.messages.insert().values(
                id=uuid.uuid4(),
                chatroom_id=scenario.visible_room_id,
                sender_type=SenderType.USER.value,
                sender_id=scenario.owner_id,
                content_md="teacher message",
                created_at=activation.created_at + dt.timedelta(seconds=1),
            )
        )
        await db.execute(
            conversation_t.guest_sessions.insert().values(
                id=guest_id,
                chatroom_id=scenario.visible_room_id,
                display_name="Guest Learner",
                refresh_token_hash=uuid.uuid4().hex,
                last_seen_at=activation.created_at + dt.timedelta(seconds=2),
            )
        )
        await db.commit()

        result = await dashboard_watchlist(
            workspace_id=scenario.workspace_id,
            principal=_principal(scenario.owner_id),
            db=db,
        )

        assert len(result.entries) == 2
        entries_by_kind = {entry.kind: entry for entry in result.entries}
        assert entries_by_kind["guest"].room_id == scenario.visible_room_id
        assert entries_by_kind["guest"].display_name == "Guest Learner"
        assert entries_by_kind["guest"].reason == "no_valid_in_round"
        assert entries_by_kind["group"].display_name == "Team North"
        assert entries_by_kind["group"].reason == "no_valid_in_round"

        await _cleanup_project_and_users(
            sessionmaker,
            scenario.project_id,
            [scenario.owner_id, scenario.member_id],
        )


@pytest.mark.asyncio
async def test_export_uses_computed_owner_and_rejects_deleted_project(
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    u = uuid.uuid4().hex[:10]
    async with sessionmaker() as db:
        users = UserRepository(db)
        org_owner = (await users.insert(email=f"td-org-owner-{u}@test.invalid", password_hash="x" * 16)).id
        project_member = (
            await users.insert(email=f"td-project-member-{u}@test.invalid", password_hash="x" * 16)
        ).id
        org_member = (await users.insert(email=f"td-org-member-{u}@test.invalid", password_hash="x" * 16)).id
        org = await OrgRepository(db).create(name=f"td-org-{u}", creator_user_id=org_owner)
        await OrgMemberRepository(db).add(
            org_id=org.id,
            user_id=org_owner,
            role=OrgMemberRole.OWNER,
            is_original_creator=True,
        )
        await OrgMemberRepository(db).add(
            org_id=org.id,
            user_id=org_member,
            role=OrgMemberRole.MEMBER,
        )
        project = await ProjectRepository(db).create(
            name=f"td-org-project-{u}",
            owner_user_id=None,
            owner_org_id=org.id,
            created_by_user_id=org_owner,
        )
        await ProjectMemberRepository(db).add(
            project_id=project.id,
            user_id=project_member,
            role=ProjectMemberRole.MEMBER,
        )
        workspace = await WorkspaceRepository(db).create(project_id=project.id, name=f"td-export-{u}")
        await db.commit()

    try:
        ctx = RequestContext(actor_ip=None)
        state = SimpleNamespace(job_id=uuid.uuid4(), status="queued")
        ws_auth_user_id = org_owner
        captured: dict = {}

        async def authenticate(_ws):
            return _ws_auth(ws_auth_user_id)

        async def capture_loop(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(ws_project_mod, "authenticate_subprotocol", authenticate)
        monkeypatch.setattr(ws_project_mod, "get_sessionmaker", lambda: sessionmaker)
        monkeypatch.setattr(ws_project_mod, "connection_loop", capture_loop)

        with (
            patch(
                "contexts.conversation.application.research_export_service.create", new_callable=AsyncMock
            ) as create,
            patch("shared_kernel.queue.enqueue", new_callable=AsyncMock),
        ):
            create.return_value = state
            async with sessionmaker() as db:
                result = await create_research_export(
                    workspace_id=workspace.id,
                    body=None,
                    ctx=ctx,
                    principal=_principal(org_owner),
                    db=db,
                )
                assert result.job_id == state.job_id

                # The inherited Org Owner reaches the socket without a project_members row.
                owner_socket = _FakeWebSocket()
                await ws_project_mod.ws_project(owner_socket, project.id)
                assert owner_socket.closed is None
                assert captured["channels"] == [f"ws:project:{project.id}"]
                assert captured["authorize_every_n_ticks"] == 1

                # A bare Org Member has no project role and receives a coded refusal.
                ws_auth_user_id = org_member
                org_member_socket = _FakeWebSocket()
                await ws_project_mod.ws_project(org_member_socket, project.id)
                assert org_member_socket.closed is not None
                assert org_member_socket.closed[0] == 4403

                # A project member is revoked at the next project-role probe.
                ws_auth_user_id = project_member
                member_socket = _FakeWebSocket()
                await ws_project_mod.ws_project(member_socket, project.id)
                connection = SimpleNamespace(principal=_principal(project_member))
                await db.execute(
                    project_members_t.delete()
                    .where(project_members_t.c.project_id == project.id)
                    .where(project_members_t.c.user_id == project_member)
                )
                await db.commit()
                assert await captured["authorize"](connection) is AccessOutcome.FORBIDDEN

                with pytest.raises(HTTPException) as forbidden:
                    await create_research_export(
                        workspace_id=workspace.id,
                        body=None,
                        ctx=ctx,
                        principal=_principal(project_member),
                        db=db,
                    )
                assert forbidden.value.status_code == 403

                await db.execute(
                    update(projects_t)
                    .where(projects_t.c.id == project.id)
                    .values(deleted_at=dt.datetime.now(dt.UTC))
                )
                await db.commit()

                with pytest.raises(HTTPException) as not_found:
                    await create_research_export(
                        workspace_id=workspace.id,
                        body=None,
                        ctx=ctx,
                        principal=_principal(org_owner),
                        db=db,
                    )
                assert not_found.value.status_code == 404

                deleted_socket = _FakeWebSocket()
                ws_auth_user_id = org_owner
                await ws_project_mod.ws_project(deleted_socket, project.id)
                assert deleted_socket.closed is not None
                assert deleted_socket.closed[0] == 4404
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(projects_t.delete().where(projects_t.c.id == project.id))
            await cleanup.execute(orgs_t.delete().where(orgs_t.c.id == org.id))
            await cleanup.execute(
                users_t.delete().where(users_t.c.id.in_([org_owner, project_member, org_member]))
            )
            await cleanup.commit()
