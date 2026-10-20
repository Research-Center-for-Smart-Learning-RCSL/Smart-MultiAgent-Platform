"""`/api/v1/workspaces/{id}/dashboard/*` -- Teacher dashboard ([R33.01]-[R33.02]).

Cross-room activity aggregation within a workspace. Three read-only endpoints;
authZ is project membership derived from the workspace's parent project.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import assert_project_role
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.dashboard.interfaces.facade import DashboardFacade
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel.auth.dependencies import current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/v1/workspaces", tags=["dashboard"])


class TrafficLight(str, enum.Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    INACTIVE = "inactive"


class RoundState(str, enum.Enum):
    ACTIVE = "active"
    ENDED = "ended"


class DashboardThresholdsOut(BaseModel):
    green_minutes: int = DashboardFacade.GREEN_MINUTES
    yellow_minutes: int = DashboardFacade.YELLOW_MINUTES
    idle_minutes: int = DashboardFacade.IDLE_MINUTES


class RoomRoundOut(BaseModel):
    activation_id: uuid.UUID
    state: RoundState
    started_at: dt.datetime
    ended_at: dt.datetime | None


class MessageCountsOut(BaseModel):
    human: int
    agent: int


class RoomSummaryOut(BaseModel):
    room_id: uuid.UUID
    room_name: str
    round: RoomRoundOut | None
    total_submissions: int
    valid_count: int
    last_valid_submission_at: dt.datetime | None
    status: TrafficLight
    online_count: int | None
    participant_count: int
    message_counts: MessageCountsOut


class DashboardSummaryOut(BaseModel):
    rooms: list[RoomSummaryOut]
    thresholds: DashboardThresholdsOut
    generated_at: dt.datetime


class TimeseriesBucketOut(BaseModel):
    room_id: uuid.UUID
    bucket: dt.datetime
    count: int


class DashboardTimeseriesOut(BaseModel):
    buckets: list[TimeseriesBucketOut]


class WatchlistEntryOut(BaseModel):
    room_id: uuid.UUID
    kind: Literal["user", "guest", "group"]
    display_name: str | None
    subject_code: str
    valid_count: int
    last_valid_submission_at: dt.datetime | None
    needs_attention: bool
    reason: Literal["no_valid_in_round", "idle"] | None


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


async def _resolve_workspace_rooms(
    *,
    db: AsyncSession,
    principal: Principal,
    workspace_id: uuid.UUID,
) -> tuple[uuid.UUID, dict[uuid.UUID, str]]:
    """The workspace's project id and its live rooms that the caller may open."""
    facade = ConversationFacade(db)
    workspace = await facade.get_workspace(workspace_id)
    if workspace is None or workspace.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if await TenancyFacade(db).get_project(workspace.project_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    await assert_project_role(db=db, principal=principal, project_id=workspace.project_id)
    rooms = await facade.visible_rooms_in_workspace(principal=principal, workspace_id=workspace_id)
    return workspace.project_id, {room.id: room.name for room in rooms}


@router.get("/{workspace_id}/dashboard/summary")
async def dashboard_summary(
    workspace_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> DashboardSummaryOut:
    project_id, room_map = await _resolve_workspace_rooms(
        db=db, principal=principal, workspace_id=workspace_id
    )
    result = await DashboardFacade(db).summary(room_map, project_id=project_id)
    return DashboardSummaryOut.model_validate(asdict(result))


@router.get("/{workspace_id}/dashboard/timeseries")
async def dashboard_timeseries(
    workspace_id: uuid.UUID = Path(...),
    window: TimeWindow = Query(TimeWindow.H1),
    bucket: BucketSize = Query(BucketSize.M5),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> DashboardTimeseriesOut:
    _, room_map = await _resolve_workspace_rooms(db=db, principal=principal, workspace_id=workspace_id)
    since = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=_WINDOW_SECONDS[window])
    buckets = await DashboardFacade(db).timeseries(
        room_map,
        since=since,
        bucket_seconds=_BUCKET_SECONDS[bucket],
    )
    return DashboardTimeseriesOut.model_validate({"buckets": [asdict(bucket) for bucket in buckets]})


@router.get("/{workspace_id}/dashboard/watchlist")
async def dashboard_watchlist(
    workspace_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> DashboardWatchlistOut:
    project_id, room_map = await _resolve_workspace_rooms(
        db=db, principal=principal, workspace_id=workspace_id
    )
    result = await DashboardFacade(db).watchlist(room_map, project_id=project_id)
    return DashboardWatchlistOut.model_validate(asdict(result))
