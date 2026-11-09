"""`/api/v1/workspaces/{id}/dashboard/*` -- Teacher dashboard ([R33.01]-[R33.02]).

Cross-room activity aggregation within a workspace. Three read-only endpoints;
authZ is project membership derived from the workspace's parent project.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import assert_project_role, is_project_owner_or_admin
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.conversation.interfaces.facade import AnalystNotice, ConversationFacade, PeerReplyReview
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


class CreativityMeansOut(BaseModel):
    """Means over the participant's latest scored attempt per round, in this room."""

    scored_rounds: int
    fluency: float | None
    flexibility: float | None
    originality: float | None
    convergence: float | None
    last_status: Literal["pending", "scored", "skipped", "error"] | None
    last_reason: str | None


class WatchlistEntryOut(BaseModel):
    room_id: uuid.UUID
    kind: Literal["user", "guest", "group"]
    display_name: str | None
    subject_code: str
    valid_count: int
    last_valid_submission_at: dt.datetime | None
    needs_attention: bool
    reason: Literal["no_valid_in_round", "idle"] | None
    # Project owner or platform admin only (Q-11); null for every other caller.
    creativity: CreativityMeansOut | None = None


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


async def _resolve_own_rooms(
    *,
    db: AsyncSession,
    principal: Principal,
    workspace_id: uuid.UUID,
) -> dict[uuid.UUID, str]:
    """The caller's own live rooms in the workspace, all of them for an admin
    ([R34.03]): observation-grade content is readable only by the room creator."""
    facade = ConversationFacade(db)
    workspace = await facade.get_workspace(workspace_id)
    if workspace is None or workspace.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if await TenancyFacade(db).get_project(workspace.project_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    await assert_project_role(db=db, principal=principal, project_id=workspace.project_id)
    rooms = await facade.visible_rooms_in_workspace(principal=principal, workspace_id=workspace_id)
    return {r.id: r.name for r in rooms if principal.is_admin or r.created_by_user_id == principal.user_id}


class AlertOut(BaseModel):
    observation_id: uuid.UUID
    room_id: uuid.UUID
    room_name: str
    agent_name: str | None
    alert_reason: str | None
    created_at: dt.datetime | None
    first_viewed_at: dt.datetime | None
    first_action_at: dt.datetime | None
    first_action_kind: (
        Literal["released", "deleted", "room_message", "activity_started", "activity_ended"] | None
    )


class DashboardAlertsOut(BaseModel):
    alerts: list[AlertOut]


@router.get("/{workspace_id}/dashboard/alerts")
async def dashboard_alerts(
    workspace_id: uuid.UUID = Path(...),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> DashboardAlertsOut:
    """Analyst alerts from the rooms the caller created ([R34.03]).

    An alert is an observation, readable only by its room's creator ([R28.03]), so
    a member who may open a room still sees none of its alerts here."""
    own = await _resolve_own_rooms(db=db, principal=principal, workspace_id=workspace_id)
    rows = await ConversationFacade(db).list_alerts_for_rooms(
        chatroom_ids=list(own), limit=limit, offset=offset
    )
    names = await AgentsFacade(db).agent_names(list({r.agent_id for r in rows}))
    return DashboardAlertsOut(
        alerts=[
            AlertOut(
                observation_id=r.id,
                room_id=r.chatroom_id,
                room_name=own[r.chatroom_id],
                agent_name=names.get(r.agent_id),
                alert_reason=r.alert_reason,
                created_at=r.created_at,
                first_viewed_at=r.first_viewed_at,
                first_action_at=r.first_action_at,
                first_action_kind=r.first_action_kind.value if r.first_action_kind else None,
            )
            for r in rows
        ]
    )


class CollaborationEntryOut(BaseModel):
    kind: Literal["analyst_notice"]
    id: uuid.UUID
    room_id: uuid.UUID
    room_name: str
    from_agent_name: str | None
    to_agent_name: str | None
    error_kind: Literal["content", "strategy"]
    error: str
    suggestion: str
    about_message_id: uuid.UUID | None
    created_at: dt.datetime
    status: Literal["pending", "answered", "unanswered"]
    decision: Literal["accepted", "partly_accepted", "declined"] | None
    reason: str | None
    responded_at: dt.datetime | None


class PeerReviewEntryOut(BaseModel):
    kind: Literal["peer_review"]
    id: uuid.UUID
    room_id: uuid.UUID
    room_name: str
    from_agent_name: str | None
    to_agent_name: str | None
    draft: str
    created_at: dt.datetime
    status: Literal["pending", "approved", "modified", "rejected", "expired"]
    attempt: int
    reason: str | None
    revised_text: str | None
    expire_reason: Literal["no_teacher", "no_decision", "binding_changed", "defer_limit", "failed"] | None
    decided_at: dt.datetime | None
    posted_message_id: uuid.UUID | None


class DashboardCollaborationOut(BaseModel):
    entries: list[Annotated[CollaborationEntryOut | PeerReviewEntryOut, Field(discriminator="kind")]]


@router.get("/{workspace_id}/dashboard/collaboration")
async def dashboard_collaboration(
    workspace_id: uuid.UUID = Path(...),
    limit: int = Query(50, ge=1, le=100),
    # The log merges two sources read to the page's end (D-10), so the offset is
    # held well below the other dashboard lists' cap.
    offset: int = Query(0, ge=0, le=1_000),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> DashboardCollaborationOut:
    """The collaboration log of the rooms the caller created ([R34.03], [R34.06]).

    Notices and held drafts are as confidential as observations ([R28.03]): only
    the room's creator reads them, whoever else may open the room."""
    own = await _resolve_own_rooms(db=db, principal=principal, workspace_id=workspace_id)
    log = await ConversationFacade(db).collaboration_log_for_rooms(
        chatroom_ids=list(own), limit=limit, offset=offset
    )
    reviews = [e for e in log if isinstance(e, PeerReplyReview)]
    notices = [e for e in log if not isinstance(e, PeerReplyReview)]
    names = await AgentsFacade(db).agent_names(
        list(
            {r.analyst_agent_id for r in notices}
            | {r.teacher_agent_id for r in notices}
            | {r.peer_agent_id for r in reviews}
            | {r.teacher_agent_id for r in reviews if r.teacher_agent_id is not None}
        )
    )
    now = dt.datetime.now(dt.UTC)

    def _notice(r: AnalystNotice) -> CollaborationEntryOut:
        return CollaborationEntryOut(
            kind="analyst_notice",
            id=r.id,
            room_id=r.chatroom_id,
            room_name=own[r.chatroom_id],
            from_agent_name=names.get(r.analyst_agent_id),
            to_agent_name=names.get(r.teacher_agent_id),
            error_kind=r.error_kind.value,
            error=r.error,
            suggestion=r.suggestion,
            about_message_id=r.about_message_id,
            created_at=r.created_at,
            status=r.status_at(now).value,
            decision=r.decision.value if r.decision else None,
            reason=r.reason,
            responded_at=r.responded_at,
        )

    def _review(r: PeerReplyReview) -> PeerReviewEntryOut:
        return PeerReviewEntryOut(
            kind="peer_review",
            id=r.id,
            room_id=r.chatroom_id,
            room_name=own[r.chatroom_id],
            from_agent_name=names.get(r.peer_agent_id),
            to_agent_name=names.get(r.teacher_agent_id) if r.teacher_agent_id else None,
            draft=r.draft,
            created_at=r.created_at,
            status=r.status_at(now).value,
            attempt=r.attempt,
            reason=r.reason,
            revised_text=r.revised_text,
            expire_reason=r.expire_reason.value if r.expire_reason else None,
            decided_at=r.decided_at,
            posted_message_id=r.posted_message_id,
        )

    return DashboardCollaborationOut(
        entries=[_review(e) if isinstance(e, PeerReplyReview) else _notice(e) for e in log]
    )


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
    include_creativity = await is_project_owner_or_admin(db=db, principal=principal, project_id=project_id)
    result = await DashboardFacade(db).watchlist(
        room_map, project_id=project_id, include_creativity=include_creativity
    )
    return DashboardWatchlistOut.model_validate(asdict(result))
