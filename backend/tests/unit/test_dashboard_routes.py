"""Dashboard current-round read-model behavior."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.dashboard import (
    BucketSize,
    TimeWindow,
    dashboard_summary,
    dashboard_timeseries,
)
from app.api.v1.dashboard import (
    TrafficLight as ApiTrafficLight,
)
from contexts.activities.interfaces.facade import ActivationStatus, RoundSubject, SubjectKind
from contexts.dashboard.application.read_service import DashboardReadService
from contexts.dashboard.domain.models import (
    DashboardActivation,
    DashboardMessageCounts,
    DashboardRoom,
    DashboardRound,
    DashboardRoundSubject,
    DashboardSubjectKind,
    DashboardSummary,
    DashboardThresholds,
    DashboardTimeseriesBucket,
    RoundState,
    TrafficLight,
)
from contexts.dashboard.interfaces.facade import DashboardFacade, _ActivitiesDashboardReader


@pytest.mark.asyncio
async def test_activity_facade_models_are_translated_at_dashboard_boundary() -> None:
    room_id = uuid.uuid4()
    activation_id = uuid.uuid4()
    user_id = uuid.uuid4()
    started_at = dt.datetime.now(dt.UTC)
    activity_facade = AsyncMock()
    activity_facade.current_activations_for_rooms.return_value = {
        room_id: SimpleNamespace(
            id=activation_id,
            status=ActivationStatus.ACTIVE,
            created_at=started_at,
            ended_at=None,
        )
    }
    activity_facade.round_session_subjects.return_value = {
        activation_id: {RoundSubject(subject_kind=SubjectKind.USER, subject_id=user_id)}
    }

    reader = _ActivitiesDashboardReader(activity_facade)
    activations = await reader.current_activations_for_rooms(chatroom_ids=[room_id])
    subjects = await reader.round_session_subjects(activation_ids=[activation_id])

    assert activations[room_id] == DashboardActivation(
        activation_id=activation_id,
        is_active=True,
        started_at=started_at,
        ended_at=None,
    )
    assert subjects[activation_id] == {
        DashboardRoundSubject(subject_id=user_id, kind=DashboardSubjectKind.USER)
    }


@pytest.mark.asyncio
async def test_summary_marks_new_active_round_green_and_ended_or_missing_round_inactive() -> None:
    now = dt.datetime.now(dt.UTC)
    active_room, ended_room, no_round_room = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    active = DashboardActivation(
        activation_id=uuid.uuid4(),
        is_active=True,
        started_at=now - dt.timedelta(minutes=2),
        ended_at=None,
    )
    ended = DashboardActivation(
        activation_id=uuid.uuid4(),
        is_active=False,
        started_at=now - dt.timedelta(hours=2),
        ended_at=now - dt.timedelta(hours=1),
    )
    activities = AsyncMock()
    activities.current_activations_for_rooms.return_value = {active_room: active, ended_room: ended}
    activities.round_aggregates.return_value = []
    session_user_id = uuid.uuid4()
    activities.round_session_subjects.return_value = {
        active.activation_id: {
            DashboardRoundSubject(subject_id=session_user_id, kind=DashboardSubjectKind.USER)
        },
    }
    conversation = AsyncMock()
    conversation.room_online_counts.return_value = {active_room: None, ended_room: 2, no_round_room: 0}
    conversation.message_counts_by_sender.return_value = {
        active_room: {"human": 3, "agent": 4},
        ended_room: {"human": 2, "agent": 1},
    }
    conversation.round_roster.return_value = {
        active_room: {(uuid.uuid4(), "guest")},
        ended_room: set(),
    }
    result = await DashboardReadService(
        activities=activities,
        conversation=conversation,
        identity=AsyncMock(),
        tenancy=AsyncMock(),
        idle_minutes=10,
    ).summary(
        {
            active_room: "Active",
            ended_room: "Ended",
            no_round_room: "Not started",
        }
    )

    by_room = {room.room_id: room for room in result.rooms}
    assert by_room[active_room].status is TrafficLight.GREEN
    assert by_room[active_room].round is not None
    assert by_room[active_room].round.state.value == "active"
    assert by_room[ended_room].status is TrafficLight.INACTIVE
    assert by_room[ended_room].round is not None
    assert by_room[no_round_room].status is TrafficLight.INACTIVE
    assert by_room[no_round_room].round is None
    assert by_room[active_room].online_count is None
    assert by_room[active_room].participant_count == 2
    assert by_room[active_room].message_counts.human == 3
    assert by_room[active_room].message_counts.agent == 4
    assert by_room[no_round_room].message_counts.human == 0
    assert by_room[no_round_room].message_counts.agent == 0
    assert result.thresholds.green_minutes == 5
    assert result.thresholds.yellow_minutes == 15


@pytest.mark.asyncio
async def test_summary_route_maps_facade_read_model_to_api_response() -> None:
    room_id = uuid.uuid4()
    now = dt.datetime.now(dt.UTC)
    summary = DashboardSummary(
        rooms=[
            DashboardRoom(
                room_id=room_id,
                room_name="Classroom",
                round=DashboardRound(
                    activation_id=uuid.uuid4(),
                    state=RoundState.ACTIVE,
                    started_at=now,
                    ended_at=None,
                ),
                total_submissions=4,
                valid_count=3,
                last_valid_submission_at=now,
                status=TrafficLight.GREEN,
                online_count=None,
                participant_count=2,
                message_counts=DashboardMessageCounts(human=3, agent=1),
            )
        ],
        thresholds=DashboardThresholds(green_minutes=5, yellow_minutes=15, idle_minutes=10),
        generated_at=now,
    )
    with (
        patch(
            "app.api.v1.dashboard._resolve_workspace_rooms",
            new=AsyncMock(return_value={room_id: "Classroom"}),
        ),
        patch.object(DashboardFacade, "summary", new=AsyncMock(return_value=summary)),
    ):
        result = await dashboard_summary(
            workspace_id=uuid.uuid4(), principal=SimpleNamespace(), db=AsyncMock()
        )

    assert result.rooms[0].room_id == room_id
    assert result.rooms[0].status is ApiTrafficLight.GREEN
    assert result.rooms[0].round is not None
    assert result.rooms[0].round.state.value == "active"
    assert result.rooms[0].message_counts.human == 3


@pytest.mark.asyncio
async def test_timeseries_route_maps_facade_buckets_to_api_response() -> None:
    room_id = uuid.uuid4()
    bucket_at = dt.datetime(2026, 10, 20, tzinfo=dt.UTC)
    with (
        patch(
            "app.api.v1.dashboard._resolve_workspace_rooms",
            new=AsyncMock(return_value={room_id: "Classroom"}),
        ),
        patch.object(
            DashboardFacade,
            "timeseries",
            new=AsyncMock(
                return_value=[DashboardTimeseriesBucket(room_id=room_id, bucket=bucket_at, count=7)]
            ),
        ),
    ):
        result = await dashboard_timeseries(
            workspace_id=uuid.uuid4(),
            window=TimeWindow.H1,
            bucket=BucketSize.M5,
            principal=SimpleNamespace(),
            db=AsyncMock(),
        )

    assert result.buckets[0].room_id == room_id
    assert result.buckets[0].bucket == bucket_at
    assert result.buckets[0].count == 7
