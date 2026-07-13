"""`/api/projects/{id}/activity-types` + `/api/chatrooms/{id}/activity-*` — §30.

Structured-activities API. Type registration/list is project-scoped (owner to
register, membership to list); sessions and submissions are room-scoped through
the conversation access chain (``ensure_can_send`` to write, ``ensure_can_read``
to read). Submission commits atomically (submission + SYSTEM echo + audit); the
WS ``activity.created`` emit and the async validation enqueue are post-commit.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams, assert_project_membership, assert_project_owner
from contexts.activities.domain.errors import SessionNotFound
from contexts.activities.domain.models import (
    ActivityAggregate,
    ActivitySession,
    ActivitySubmission,
    ActivityType,
    ValidatorKind,
)
from contexts.activities.interfaces.facade import ActivitiesFacade
from contexts.conversation.application.access import (
    ensure_can_read,
    ensure_can_send,
    resolve_room_access,
)
from contexts.conversation.interfaces import room_channel
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.queue import enqueue
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)

project_router = APIRouter(prefix="/api/projects", tags=["activities"])
chatroom_router = APIRouter(prefix="/api/chatrooms", tags=["activities"])

_MAX_KEY = 128
_MAX_NAME = 256


# --------------------------------------------------------------------------- #
# Request / response models                                                    #
# --------------------------------------------------------------------------- #


class ActivityTypeIn(BaseModel):
    key: str = Field(min_length=1, max_length=_MAX_KEY)
    name: str = Field(min_length=1, max_length=_MAX_NAME)
    payload_schema: dict[str, Any]
    validator_kind: ValidatorKind
    validator_config: dict[str, Any] = Field(default_factory=dict)
    retention_days: int | None = Field(default=None, ge=1)


class ActivityTypeOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    key: str
    name: str
    payload_schema: dict[str, Any]
    validator_kind: ValidatorKind
    validator_config: dict[str, Any]
    retention_days: int | None
    created_at: str | None


class ActivitySessionOpenIn(BaseModel):
    activity_type_id: uuid.UUID
    subject_user_id: uuid.UUID | None = None


class ActivitySessionOut(BaseModel):
    id: uuid.UUID
    activity_type_id: uuid.UUID
    chatroom_id: uuid.UUID
    subject_user_id: uuid.UUID
    status: str
    created_at: str | None
    closed_at: str | None


class ActivitySubmissionIn(BaseModel):
    activity_type_id: uuid.UUID
    payload: dict[str, Any]
    session_id: uuid.UUID | None = None
    subject_user_id: uuid.UUID | None = None


class ActivitySubmissionOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    activity_type_id: uuid.UUID
    chatroom_id: uuid.UUID
    attempt_no: int
    validation_status: str
    is_valid: bool | None
    error_class: str | None
    sub_scores: dict[str, Any]
    latency_ms: int | None
    created_at: str | None


class ActivityAggregateOut(BaseModel):
    total: int
    valid_count: int
    error_count: int
    pending_count: int
    error_class_histogram: dict[str, int]
    latency_avg_ms: float | None
    latency_min_ms: int | None
    latency_max_ms: int | None


class ActivitySubmissionsPageOut(BaseModel):
    items: list[ActivitySubmissionOut]
    aggregate: ActivityAggregateOut


def _type_out(t: ActivityType) -> ActivityTypeOut:
    return ActivityTypeOut(
        id=t.id,
        project_id=t.project_id,
        key=t.key,
        name=t.name,
        payload_schema=t.payload_schema,
        validator_kind=t.validator_kind,
        validator_config=t.validator_config,
        retention_days=t.retention_days,
        created_at=t.created_at.isoformat() if t.created_at else None,
    )


def _session_out(s: ActivitySession) -> ActivitySessionOut:
    return ActivitySessionOut(
        id=s.id,
        activity_type_id=s.activity_type_id,
        chatroom_id=s.chatroom_id,
        subject_user_id=s.subject_user_id,
        status=s.status.value,
        created_at=s.created_at.isoformat() if s.created_at else None,
        closed_at=s.closed_at.isoformat() if s.closed_at else None,
    )


def _submission_out(s: ActivitySubmission) -> ActivitySubmissionOut:
    return ActivitySubmissionOut(
        id=s.id,
        session_id=s.session_id,
        activity_type_id=s.activity_type_id,
        chatroom_id=s.chatroom_id,
        attempt_no=s.attempt_no,
        validation_status=s.validation_status.value,
        is_valid=s.is_valid,
        error_class=s.error_class,
        sub_scores=s.sub_scores,
        latency_ms=s.latency_ms,
        created_at=s.created_at.isoformat() if s.created_at else None,
    )


def _aggregate_out(a: ActivityAggregate) -> ActivityAggregateOut:
    return ActivityAggregateOut(
        total=a.total,
        valid_count=a.valid_count,
        error_count=a.error_count,
        pending_count=a.pending_count,
        error_class_histogram=a.error_class_histogram,
        latency_avg_ms=a.latency_avg_ms,
        latency_min_ms=a.latency_min_ms,
        latency_max_ms=a.latency_max_ms,
    )


# --------------------------------------------------------------------------- #
# Activity types (project-scoped)                                              #
# --------------------------------------------------------------------------- #


@project_router.post("/{project_id}/activity-types")
async def register_activity_type(
    body: ActivityTypeIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityTypeOut:
    await assert_project_owner(db=db, principal=principal, project_id=project_id)
    activity_type = await ActivitiesFacade(db).register_type(
        project_id=project_id,
        key=body.key,
        name=body.name,
        payload_schema=body.payload_schema,
        validator_kind=body.validator_kind,
        validator_config=body.validator_config,
        retention_days=body.retention_days,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    return _type_out(activity_type)


@project_router.get("/{project_id}/activity-types")
async def list_activity_types(
    project_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[ActivityTypeOut]:
    await assert_project_membership(db=db, principal=principal, project_id=project_id)
    types = await ActivitiesFacade(db).list_types(project_id)
    return [_type_out(t) for t in types]


# --------------------------------------------------------------------------- #
# Sessions (room-scoped)                                                       #
# --------------------------------------------------------------------------- #


@chatroom_router.post("/{chatroom_id}/activity-sessions")
async def open_activity_session(
    body: ActivitySessionOpenIn,
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivitySessionOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    session = await ActivitiesFacade(db).open_session(
        project_id=access.project_id,
        activity_type_id=body.activity_type_id,
        chatroom_id=chatroom_id,
        subject_user_id=body.subject_user_id or principal.user_id,
    )
    await db.commit()
    return _session_out(session)


@chatroom_router.patch("/{chatroom_id}/activity-sessions/{session_id}/close")
async def close_activity_session(
    chatroom_id: uuid.UUID = Path(...),
    session_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivitySessionOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    facade = ActivitiesFacade(db)
    await facade.close_session(session_id=session_id, chatroom_id=chatroom_id)
    await db.commit()
    session = await facade.get_session(session_id)
    if session is None:  # unreachable — close_session validated existence in this txn
        raise SessionNotFound(str(session_id))
    return _session_out(session)


# --------------------------------------------------------------------------- #
# Submissions (room-scoped)                                                    #
# --------------------------------------------------------------------------- #


@chatroom_router.post("/{chatroom_id}/activity-submissions")
async def submit_activity(
    body: ActivitySubmissionIn,
    chatroom_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivitySubmissionOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    submission = await ActivitiesFacade(db).submit(
        project_id=access.project_id,
        activity_type_id=body.activity_type_id,
        chatroom_id=chatroom_id,
        producer_user_id=principal.user_id,
        subject_user_id=body.subject_user_id or principal.user_id,
        payload=body.payload,
        session_id=body.session_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    # Durable-commit before dispatch (mirrors send_message): the client refetch
    # and the validation worker must see the committed rows.
    await db.commit()
    await _dispatch_submission(chatroom_id, submission)
    return _submission_out(submission)


@chatroom_router.get("/{chatroom_id}/activity-submissions")
async def list_activity_submissions(
    chatroom_id: uuid.UUID = Path(...),
    session_id: uuid.UUID | None = Query(default=None),
    subject_user_id: uuid.UUID | None = Query(default=None),
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivitySubmissionsPageOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    facade = ActivitiesFacade(db)
    items = await facade.list_submissions(
        chatroom_id=chatroom_id,
        session_id=session_id,
        subject_user_id=subject_user_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    aggregate = await facade.aggregate(
        chatroom_id=chatroom_id, session_id=session_id, subject_user_id=subject_user_id
    )
    return ActivitySubmissionsPageOut(
        items=[_submission_out(s) for s in items], aggregate=_aggregate_out(aggregate)
    )


async def _dispatch_submission(chatroom_id: uuid.UUID, submission: ActivitySubmission) -> None:
    """Post-commit fan-out — best-effort: the submission is committed, so a Redis
    or pub/sub hiccup must never surface as a failed submission."""
    try:
        await Publisher(room_channel(chatroom_id)).emit(
            "activity.created",
            {
                "submission_id": str(submission.id),
                "activity_type_id": str(submission.activity_type_id),
                "validation_status": submission.validation_status.value,
            },
        )
    except Exception:
        _log.error("realtime publish failed for activity submission %s", submission.id, exc_info=True)
    if submission.validation_status.value == "pending":
        try:
            await enqueue("validate_activity_submission", str(submission.id))
        except Exception:
            _log.warning("activity validation enqueue failed for %s", submission.id, exc_info=True)


__all__ = ["chatroom_router", "project_router"]
