"""`/api/v1/projects/{id}/dashboard/*` -- Teacher dashboard ([R33.01]-[R33.02]).

Cross-room activity aggregation for facilitators. Three read-only endpoints;
authZ is project membership plus created_by_user_id filtering (only rooms
the caller created).
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import assert_project_membership
from contexts.activities.interfaces.facade import ActivitiesFacade
from contexts.conversation.interfaces.facade import ConversationFacade
from shared_kernel.auth.dependencies import current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/v1/projects", tags=["dashboard"])

_STALE_MINUTES_GREEN = 5
_STALE_MINUTES_YELLOW = 15
_ALERT_MINUTES = 10


class TrafficLight(str, enum.Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class RoomSummaryOut(BaseModel):
    room_id: uuid.UUID
    room_name: str
    total_submissions: int
    valid_count: int
    last_submission_at: dt.datetime | None
    status: TrafficLight


class DashboardSummaryOut(BaseModel):
    rooms: list[RoomSummaryOut]


class TimeseriesBucketOut(BaseModel):
    room_id: uuid.UUID
    bucket: dt.datetime
    count: int


class DashboardTimeseriesOut(BaseModel):
    buckets: list[TimeseriesBucketOut]


class WatchlistEntryOut(BaseModel):
    subject_code: str
    submission_count: int
    last_submission_at: dt.datetime | None
    needs_attention: bool


class DashboardWatchlistOut(BaseModel):
    entries: list[WatchlistEntryOut]


class TimeWindow(str, enum.Enum):
    H1 = "1h"
    H6 = "6h"
    H24 = "24h"


class BucketSize(str, enum.Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"


_WINDOW_SECONDS = {
    TimeWindow.H1: 3600,
    TimeWindow.H6: 6 * 3600,
    TimeWindow.H24: 24 * 3600,
}

_BUCKET_SECONDS = {
    BucketSize.M1: 60,
    BucketSize.M5: 300,
    BucketSize.M15: 900,
    BucketSize.H1: 3600,
}


def _traffic_light(last_submission_at: dt.datetime | None) -> TrafficLight:
    if last_submission_at is None:
        return TrafficLight.RED
    elapsed = (dt.datetime.now(dt.UTC) - last_submission_at).total_seconds() / 60
    if elapsed <= _STALE_MINUTES_GREEN:
        return TrafficLight.GREEN
    if elapsed <= _STALE_MINUTES_YELLOW:
        return TrafficLight.YELLOW
    return TrafficLight.RED


async def _resolve_facilitator_rooms(
    *,
    db: AsyncSession,
    principal: Principal,
    project_id: uuid.UUID,
) -> dict[uuid.UUID, str]:
    """Chatroom IDs and names for rooms the caller created in this project."""
    room_ids = await ConversationFacade(db).list_chatroom_ids_for_project(project_id)
    if not room_ids:
        return {}
    rooms = await ConversationFacade(db).get_chatrooms(room_ids)
    return {rid: room.name for rid, room in rooms.items() if room.created_by_user_id == principal.user_id}


@router.get("/{project_id}/dashboard/summary")
async def dashboard_summary(
    project_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> DashboardSummaryOut:
    await assert_project_membership(db=db, principal=principal, project_id=project_id)
    room_map = await _resolve_facilitator_rooms(db=db, principal=principal, project_id=project_id)
    if not room_map:
        return DashboardSummaryOut(rooms=[])

    aggregates = await ActivitiesFacade(db).aggregate_for_rooms(chatroom_ids=list(room_map.keys()))
    agg_by_room = {a.chatroom_id: a for a in aggregates}

    rooms: list[RoomSummaryOut] = []
    for rid, name in room_map.items():
        agg = agg_by_room.get(rid)
        last_at = agg.last_submission_at if agg else None
        rooms.append(
            RoomSummaryOut(
                room_id=rid,
                room_name=name,
                total_submissions=agg.total_submissions if agg else 0,
                valid_count=agg.valid_count if agg else 0,
                last_submission_at=last_at,
                status=_traffic_light(last_at),
            )
        )
    return DashboardSummaryOut(rooms=rooms)


@router.get("/{project_id}/dashboard/timeseries")
async def dashboard_timeseries(
    project_id: uuid.UUID = Path(...),
    window: TimeWindow = Query(TimeWindow.H1),
    bucket: BucketSize = Query(BucketSize.M5),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> DashboardTimeseriesOut:
    await assert_project_membership(db=db, principal=principal, project_id=project_id)
    room_map = await _resolve_facilitator_rooms(db=db, principal=principal, project_id=project_id)
    if not room_map:
        return DashboardTimeseriesOut(buckets=[])

    since = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=_WINDOW_SECONDS[window])
    ts = await ActivitiesFacade(db).timeseries_for_rooms(
        chatroom_ids=list(room_map.keys()),
        since=since,
        bucket_seconds=_BUCKET_SECONDS[bucket],
    )
    return DashboardTimeseriesOut(
        buckets=[TimeseriesBucketOut(room_id=b.chatroom_id, bucket=b.bucket, count=b.count) for b in ts]
    )


@router.get("/{project_id}/dashboard/watchlist")
async def dashboard_watchlist(
    project_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> DashboardWatchlistOut:
    await assert_project_membership(db=db, principal=principal, project_id=project_id)
    room_map = await _resolve_facilitator_rooms(db=db, principal=principal, project_id=project_id)
    if not room_map:
        return DashboardWatchlistOut(entries=[])

    entries = await ActivitiesFacade(db).watchlist_for_rooms(
        chatroom_ids=list(room_map.keys()),
    )
    return DashboardWatchlistOut(
        entries=[
            WatchlistEntryOut(
                subject_code=e.subject_code,
                submission_count=e.submission_count,
                last_submission_at=e.last_submission_at,
                needs_attention=True,
            )
            for e in entries
        ]
    )
