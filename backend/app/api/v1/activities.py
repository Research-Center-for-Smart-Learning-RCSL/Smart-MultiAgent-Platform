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
from contexts.activities.domain.errors import SessionNotFound, ValidatorConfigInvalid
from contexts.activities.domain.models import (
    ActivityActivation,
    ActivityAggregate,
    ActivitySession,
    ActivitySubmission,
    ActivityType,
    ValidatorKind,
)
from contexts.activities.interfaces.facade import ActivitiesFacade
from contexts.conversation.interfaces import room_channel
from contexts.conversation.interfaces.access import (
    ensure_can_read,
    ensure_can_send,
    ensure_room_creator,
    resolve_room_access,
)
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


class ActivityActivationStartIn(BaseModel):
    activity_type_id: uuid.UUID


class ActivityActivationOut(BaseModel):
    id: uuid.UUID
    chatroom_id: uuid.UUID
    activity_type_id: uuid.UUID
    started_by_user_id: uuid.UUID
    status: str
    created_at: str | None
    ended_at: str | None


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


def _activation_out(a: ActivityActivation) -> ActivityActivationOut:
    return ActivityActivationOut(
        id=a.id,
        chatroom_id=a.chatroom_id,
        activity_type_id=a.activity_type_id,
        started_by_user_id=a.started_by_user_id,
        status=a.status.value,
        created_at=a.created_at.isoformat() if a.created_at else None,
        ended_at=a.ended_at.isoformat() if a.ended_at else None,
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
    if body.validator_kind is ValidatorKind.MCP:
        await _assert_mcp_binding_in_project(db, project_id, body.validator_config)
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


async def _assert_mcp_binding_in_project(
    db: AsyncSession, project_id: uuid.UUID, config: dict[str, Any]
) -> None:
    """Reject an ``mcp`` validator whose agent/binding belongs to another project
    (R30.24). The activities context cannot see the agents context, so this
    cross-context check lives at the route: without it an owner could POST a
    foreign ``binding_id`` directly, bypassing the UI's project-scoped pickers.

    A malformed (non-UUID) config returns early so ``type_service`` raises the
    precise ``ValidatorConfigInvalid`` for it — this guard only adds the
    project-membership dimension.
    """
    from contexts.agents.domain.models import AgentToolType
    from contexts.agents.interfaces.facade import AgentsFacade

    try:
        agent_id = uuid.UUID(str(config["agent_id"]))
        binding_id = uuid.UUID(str(config["binding_id"]))
    except (KeyError, ValueError, TypeError):
        return

    facade = AgentsFacade(db)
    agent = await facade.get_agent(agent_id)
    if agent is None or agent.project_id != project_id:
        raise ValidatorConfigInvalid("mcp validator agent_id is not an agent in this project")
    tools = await facade.list_agent_tools(agent_id)
    if not any(t.id == binding_id and t.tool_type is AgentToolType.HOSTED_MCP for t in tools):
        raise ValidatorConfigInvalid("mcp validator binding_id is not a hosted_mcp tool on this agent")


@project_router.delete("/{project_id}/activity-types/{type_id}", status_code=204, response_model=None)
async def delete_activity_type(
    project_id: uuid.UUID = Path(...),
    type_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await assert_project_owner(db=db, principal=principal, project_id=project_id)
    ended = await ActivitiesFacade(db).delete_type(
        project_id=project_id,
        type_id=type_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    # Durable-commit before the WS fan-out: the tombstone and every activation-end
    # must be persisted before any room is told its activation ended.
    await db.commit()
    for chatroom_id, activation_id in ended:
        await _dispatch_activation_ended(chatroom_id, activation_id)


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


@chatroom_router.post("/{chatroom_id}/activity-activations")
async def start_activity_activation(
    body: ActivityActivationStartIn,
    chatroom_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityActivationOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_room_creator(access, principal=principal)
    activation = await ActivitiesFacade(db).start_activation(
        project_id=access.project_id,
        chatroom_id=chatroom_id,
        activity_type_id=body.activity_type_id,
        started_by_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    await _dispatch_activation_started(activation)
    return _activation_out(activation)


@chatroom_router.patch("/{chatroom_id}/activity-activations/{activation_id}/end")
async def end_activity_activation(
    chatroom_id: uuid.UUID = Path(...),
    activation_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityActivationOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_room_creator(access, principal=principal)
    result = await ActivitiesFacade(db).end_activation(
        chatroom_id=chatroom_id,
        activation_id=activation_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    if result.transitioned:
        await _dispatch_activation_ended(chatroom_id, result.activation.id)
    return _activation_out(result.activation)


@chatroom_router.get("/{chatroom_id}/activity-activations/active")
async def get_active_activity_activation(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityActivationOut | None:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    activation = await ActivitiesFacade(db).get_active_activation(chatroom_id)
    return _activation_out(activation) if activation is not None else None


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
        caller_user_id=None if principal.is_admin else principal.user_id,
    )
    await db.commit()
    return _session_out(session)


@chatroom_router.patch("/{chatroom_id}/activity-sessions/{session_id}/close")
async def close_activity_session(
    chatroom_id: uuid.UUID = Path(...),
    session_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivitySessionOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    facade = ActivitiesFacade(db)
    await facade.close_session(
        session_id=session_id,
        chatroom_id=chatroom_id,
        subject_user_id=None if principal.is_admin else principal.user_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
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
    submission, signal_payload = await ActivitiesFacade(db).submit(
        project_id=access.project_id,
        activity_type_id=body.activity_type_id,
        chatroom_id=chatroom_id,
        producer_user_id=principal.user_id,
        subject_user_id=body.subject_user_id or principal.user_id,
        caller_user_id=None if principal.is_admin else principal.user_id,
        payload=body.payload,
        session_id=body.session_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    # Durable-commit before dispatch (mirrors send_message): the client refetch
    # and the validation worker must see the committed rows.
    await db.commit()
    await _dispatch_submission(chatroom_id, submission, signal_payload)
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


async def _dispatch_submission(
    chatroom_id: uuid.UUID, submission: ActivitySubmission, signal_payload: dict[str, Any]
) -> None:
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
    # Reactive-rules signal (R30.12): the submit-time emit, using the payload
    # ``submit`` already built (no re-fetch). For an in_process type the verdict is
    # final here (rolling reflects it); for an async type it carries
    # validation_status=pending. The completion emit with the final error_class
    # comes later from the validation worker. A dropped signal must never fail a
    # committed submission (mirrors the message-signal path in ``messages.py``).
    try:
        await enqueue("workflow_signal", "activity", signal_payload)
    except Exception:
        _log.warning("activity workflow-signal dispatch failed for %s", submission.id, exc_info=True)


async def _dispatch_activation_started(activation: ActivityActivation) -> None:
    try:
        await Publisher(room_channel(activation.chatroom_id)).emit(
            "activity.activation.started",
            {
                "activation_id": str(activation.id),
                "activity_type_id": str(activation.activity_type_id),
                "started_by": str(activation.started_by_user_id),
            },
        )
    except Exception:
        _log.error("realtime publish failed for activity activation %s", activation.id, exc_info=True)


async def _dispatch_activation_ended(chatroom_id: uuid.UUID, activation_id: uuid.UUID) -> None:
    try:
        await Publisher(room_channel(chatroom_id)).emit(
            "activity.activation.ended", {"activation_id": str(activation_id)}
        )
    except Exception:
        _log.error("realtime publish failed for ended activity activation %s", activation_id, exc_info=True)


__all__ = ["chatroom_router", "project_router"]
