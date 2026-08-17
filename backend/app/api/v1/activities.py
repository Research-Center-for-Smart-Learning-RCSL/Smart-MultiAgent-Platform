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

from app.api.v1.deps import (
    PaginationParams,
    assert_project_membership,
    assert_project_owner,
    is_project_owner_or_admin,
)
from contexts.activities.domain.errors import (
    SessionNotFound,
    ValidatorConfigInvalid,
)
from contexts.activities.domain.models import (
    ActivityActivation,
    ActivityAggregate,
    ActivitySession,
    ActivitySubmission,
    ActivityType,
    ActivityTypeScope,
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
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.identity.interfaces import user_channel
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.queue import enqueue
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)

project_router = APIRouter(prefix="/api/projects", tags=["activities"])
chatroom_router = APIRouter(prefix="/api/chatrooms", tags=["activities"])
validator_router = APIRouter(prefix="/api", tags=["activities"])

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
    expose_payload_to_agent: bool = True
    echo_includes_content: bool = False


class ActivityTypeUpdateIn(BaseModel):
    """Full editable representation for an edit; ``key`` is intentionally absent
    (never editable, R30.23). The edit form pre-fills and resubmits every field,
    so the service diffs this against the stored row to decide the version bump."""

    name: str = Field(min_length=1, max_length=_MAX_NAME)
    payload_schema: dict[str, Any]
    validator_kind: ValidatorKind
    validator_config: dict[str, Any] = Field(default_factory=dict)
    retention_days: int | None = Field(default=None, ge=1)
    expose_payload_to_agent: bool = True
    echo_includes_content: bool = False


class ActivityTypeOut(BaseModel):
    id: uuid.UUID
    # None exactly when `scope` is `platform`: a shipped example installed by a
    # platform admin has no owning project ([R30.02]).
    project_id: uuid.UUID | None
    scope: ActivityTypeScope
    key: str
    name: str
    payload_schema: dict[str, Any]
    validator_kind: ValidatorKind
    validator_config: dict[str, Any]
    retention_days: int | None
    expose_payload_to_agent: bool
    echo_includes_content: bool
    created_at: str | None


class ActivityTypeRegisteredOut(ActivityTypeOut):
    """The registration response: the created type plus any advisory warning.

    A subclass rather than a wrapper so the client keeps reading the row exactly
    where it always did. `shadowed_by_platform` is not an error: the type WAS
    created ([R30.02] permits the collision), but the project now holds two live
    types under one key and everything that selects by key alone selects both.
    """

    shadowed_by_platform: bool = False


class ActivityTypeOptInResultOut(BaseModel):
    """The opt-in response. 200 with a body rather than 204, so the mirror of
    `ActivityTypeRegisteredOut.shadowed_by_platform` has somewhere to go."""

    shadows_owned_key: bool = False


class ActivityTypePublicOut(BaseModel):
    """The participant rendering contract (R30.26): identity, key, display
    name, and payload schema, and nothing else. No `validator_config` — that
    field is confidential to Project Owners (R30.25). Reachable through the
    room-access chain, never through project membership."""

    id: uuid.UUID
    key: str
    name: str
    payload_schema: dict[str, Any]


class ActivityValidatorOut(BaseModel):
    """A registered first-party in-process validator the authoring form may offer."""

    id: str
    title: str


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
    # The rendering contract embedded so a participant needs no round trip
    # (Q-1); `None` only when the type row is missing or cross-project, same
    # as a null activation would be treated.
    activity_type: ActivityTypePublicOut | None = None


class ActivitySessionOut(BaseModel):
    id: uuid.UUID
    activity_type_id: uuid.UUID
    chatroom_id: uuid.UUID
    subject_user_id: uuid.UUID
    # The round this session was answered under (0077). Null only on a pre-0077
    # row, which no live surface can reach.
    activation_id: uuid.UUID | None
    status: str
    created_at: str | None
    closed_at: str | None
    # When the subject declared themselves finished; null while they have not,
    # have undone it, or have submitted since ([R30.22]).
    completed_at: str | None


class ActivitySessionCompletionIn(BaseModel):
    completed: bool
    subject_user_id: uuid.UUID | None = None


class ActivityActivationProgressOut(BaseModel):
    """How one round is going, as its facilitator sees it ([R30.22]).

    Counts only. Naming who has finished is a separate privacy decision that the
    room-creator gate does not by itself authorize, so it is not in this model
    and must not be added to it without one.
    """

    completed: int
    in_progress: int


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


def _type_out(t: ActivityType, *, include_validator_config: bool = True) -> ActivityTypeOut:
    return ActivityTypeOut(
        id=t.id,
        project_id=t.project_id,
        scope=t.scope,
        key=t.key,
        name=t.name,
        payload_schema=t.payload_schema,
        validator_kind=t.validator_kind,
        validator_config=t.validator_config if include_validator_config else {},
        retention_days=t.retention_days,
        expose_payload_to_agent=t.expose_payload_to_agent,
        echo_includes_content=t.echo_includes_content,
        created_at=t.created_at.isoformat() if t.created_at else None,
    )


def _type_public_out(t: ActivityType) -> ActivityTypePublicOut:
    return ActivityTypePublicOut(id=t.id, key=t.key, name=t.name, payload_schema=t.payload_schema)


def _session_out(s: ActivitySession) -> ActivitySessionOut:
    return ActivitySessionOut(
        id=s.id,
        activity_type_id=s.activity_type_id,
        chatroom_id=s.chatroom_id,
        subject_user_id=s.subject_user_id,
        activation_id=s.activation_id,
        status=s.status.value,
        created_at=s.created_at.isoformat() if s.created_at else None,
        closed_at=s.closed_at.isoformat() if s.closed_at else None,
        completed_at=s.completed_at.isoformat() if s.completed_at else None,
    )


def _activation_out(a: ActivityActivation, activity_type: ActivityType | None) -> ActivityActivationOut:
    return ActivityActivationOut(
        id=a.id,
        chatroom_id=a.chatroom_id,
        activity_type_id=a.activity_type_id,
        started_by_user_id=a.started_by_user_id,
        status=a.status.value,
        created_at=a.created_at.isoformat() if a.created_at else None,
        ended_at=a.ended_at.isoformat() if a.ended_at else None,
        activity_type=_type_public_out(activity_type) if activity_type is not None else None,
    )


async def _resolve_activation_type(
    facade: ActivitiesFacade, *, project_id: uuid.UUID, activation: ActivityActivation
) -> ActivityType | None:
    """Tenant-safe lookup for embedding in an activation read/broadcast: the
    type is fetched fresh rather than trusted from the activation row, so a
    type that went missing or became unreachable from this project never leaks
    through.

    Goes through the reachability resolver rather than comparing ``project_id``:
    a platform-scoped type has none, so the old comparison would embed
    ``activity_type: null`` for every installed example and leave participants
    with no rendering contract ([R30.26], [R30.33]).

    Called post-commit at two call sites (start/end): a transient failure here
    must not turn an already-committed activation change into a 500 for the
    facilitator, so it degrades to no embedded type rather than propagating —
    the client's fallback room-scoped read (Q-1) recovers it. ``ActivityTypeNotFound``
    lands in the same arm by design: the correct response to "not reachable" is
    an absent embed, which is what a null already meant.
    """
    try:
        return await facade.resolve_type_for_project(
            project_id=project_id, activity_type_id=activation.activity_type_id
        )
    except Exception:
        _log.warning("activity type resolution failed for embed, activation %s", activation.id, exc_info=True)
        return None


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
) -> ActivityTypeRegisteredOut:
    await assert_project_owner(db=db, principal=principal, project_id=project_id)
    if body.validator_kind is ValidatorKind.MCP:
        await _assert_mcp_binding_in_project(db, project_id, body.validator_config)
    registration = await ActivitiesFacade(db).register_type(
        project_id=project_id,
        key=body.key,
        name=body.name,
        payload_schema=body.payload_schema,
        validator_kind=body.validator_kind,
        validator_config=body.validator_config,
        retention_days=body.retention_days,
        expose_payload_to_agent=body.expose_payload_to_agent,
        echo_includes_content=body.echo_includes_content,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    return ActivityTypeRegisteredOut(
        **_type_out(registration.activity_type).model_dump(),
        shadowed_by_platform=registration.shadowed_by_platform,
    )


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


@project_router.patch("/{project_id}/activity-types/{type_id}")
async def update_activity_type(
    body: ActivityTypeUpdateIn,
    project_id: uuid.UUID = Path(...),
    type_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityTypeOut:
    await assert_project_owner(db=db, principal=principal, project_id=project_id)
    if body.validator_kind is ValidatorKind.MCP:
        await _assert_mcp_binding_in_project(db, project_id, body.validator_config)
    activity_type = await ActivitiesFacade(db).update_type(
        project_id=project_id,
        type_id=type_id,
        name=body.name,
        payload_schema=body.payload_schema,
        validator_kind=body.validator_kind,
        validator_config=body.validator_config,
        retention_days=body.retention_days,
        expose_payload_to_agent=body.expose_payload_to_agent,
        echo_includes_content=body.echo_includes_content,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    return _type_out(activity_type)


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
        await dispatch_activation_ended(chatroom_id, activation_id)


@project_router.get("/{project_id}/activity-types")
async def list_activity_types(
    project_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[ActivityTypeOut]:
    """Membership gate is unchanged — a non-owner member still legitimately
    lists types (that is how a facilitator picks one to activate). Only
    `validator_config` is owner-gated (R30.25): it may hold answer keys and,
    once sealed validator credentials exist, secrets."""
    await assert_project_membership(db=db, principal=principal, project_id=project_id)
    is_owner = await is_project_owner_or_admin(db=db, principal=principal, project_id=project_id)
    types = await ActivitiesFacade(db).list_types(project_id)
    return [_type_out(t, include_validator_config=is_owner) for t in types]


class PlatformExampleOut(BaseModel):
    """An installed platform example as a Project Owner sees it ([R30.32]).

    Carries the two governance flags because enabling one is a consent decision:
    ``expose_payload_to_agent`` means participant text reaches the project's
    configured LLM provider, and the owner making that choice has to be told so at
    the moment they make it.

    No ``validator_config`` — it may hold answer keys and is owner-confidential
    ([R30.25]). Its absence here is not a redaction to re-add later: this listing
    exists to choose a type, not to inspect one.
    """

    id: uuid.UUID
    key: str
    name: str
    expose_payload_to_agent: bool
    echo_includes_content: bool
    retention_days: int | None
    enabled: bool


class ActivityTypeOptInIn(BaseModel):
    activity_type_id: uuid.UUID


@project_router.get("/{project_id}/activity-examples")
async def list_platform_activity_examples(
    project_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[PlatformExampleOut]:
    """The installed platform examples, with this project's enabled state.

    Project Owner rather than plain membership: the only thing this listing is for
    is deciding what to enable, which is the owner's call ([R30.23]). It is also
    why the catalogue being visible to every owner is acceptable — installed
    examples are platform metadata, not another tenant's data (OQ-2).
    """
    await assert_project_owner(db=db, principal=principal, project_id=project_id)
    examples = await ActivitiesFacade(db).list_platform_examples_for_project(project_id)
    return [
        PlatformExampleOut(
            id=e.activity_type.id,
            key=e.activity_type.key,
            name=e.activity_type.name,
            expose_payload_to_agent=e.activity_type.expose_payload_to_agent,
            echo_includes_content=e.activity_type.echo_includes_content,
            retention_days=e.activity_type.retention_days,
            enabled=e.enabled,
        )
        for e in examples
    ]


@project_router.post("/{project_id}/activity-type-optins")
async def opt_project_into_activity_type(
    body: ActivityTypeOptInIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityTypeOptInResultOut:
    """Enable a platform example for this project ([R30.33]).

    200 with a body rather than the 204 this used to return: opting into a key
    the project already owns is permitted but leaves two live types under one key
    ([R30.02]), and the owner has to be told at the moment they do it.
    """
    await assert_project_owner(db=db, principal=principal, project_id=project_id)
    shadows_owned_key = await ActivitiesFacade(db).opt_project_in(
        project_id=project_id,
        activity_type_id=body.activity_type_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    return ActivityTypeOptInResultOut(shadows_owned_key=shadows_owned_key)


@project_router.delete("/{project_id}/activity-type-optins/{type_id}", status_code=204, response_model=None)
async def opt_project_out_of_activity_type(
    project_id: uuid.UUID = Path(...),
    type_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    """Disable a platform example for this project, ending only its activations.

    Same post-commit ordering as ``delete_activity_type``: the opt-in removal and
    every activation-end must be durable before any room is told its activation
    ended.
    """
    await assert_project_owner(db=db, principal=principal, project_id=project_id)
    ended = await ActivitiesFacade(db).opt_project_out(
        project_id=project_id,
        activity_type_id=type_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    for chatroom_id, activation_id in ended:
        await dispatch_activation_ended(chatroom_id, activation_id)


@chatroom_router.get("/{chatroom_id}/activity-types/{type_id}")
async def get_room_activity_type(
    chatroom_id: uuid.UUID = Path(...),
    type_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityTypePublicOut:
    """Room-scoped rendering-contract read (R30.26, Q-1): the recovery path
    when the activation-started broadcast was missed, the store was reset, or
    a future flow needs a type that is not the currently active one. Gated by
    the room-access chain, not project membership, so a chatroom guest is a
    full activity participant."""
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    # Same gate as the write paths ([R30.33]): the id comes from the client, and a
    # platform type has no project_id to compare against.
    activity_type = await ActivitiesFacade(db).resolve_type_for_project(
        project_id=access.project_id, activity_type_id=type_id
    )
    return _type_public_out(activity_type)


# --------------------------------------------------------------------------- #
# Validators (process-global, first-party)                                     #
# --------------------------------------------------------------------------- #


@validator_router.get("/activity-validators")
async def list_activity_validators(
    principal: Principal = Depends(current_principal),
) -> list[ActivityValidatorOut]:
    """List the registered first-party in-process validators (R30.24). Global and
    process-scoped — availability never varies per project — so any authenticated
    caller reads the same set the picker draws from. Exposes ids/titles only."""
    return [
        ActivityValidatorOut(id=v.validator_id, title=v.title) for v in ActivitiesFacade.list_validators()
    ]


class ActivityPolicyPublicOut(BaseModel):
    """The platform governance policy as an author needs to see it ([R30.29]).

    Any authenticated caller, like the validator listing above: the policy is
    platform configuration, not a secret, and an owner would learn the same facts
    from a 409 on their first save. Reading it up front is what lets the authoring
    form pre-fill a default and disable a locked switch instead of letting the
    owner fill in a form that cannot be accepted.

    Deliberately omits ``updated_by_user_id`` — who set the policy is an admin
    concern and is on the admin surface.
    """

    expose_payload_to_agent_default: bool
    expose_payload_to_agent_locked: bool
    echo_includes_content_default: bool
    echo_includes_content_locked: bool
    retention_days_default: int | None
    retention_days_max: int | None


@validator_router.get("/activity-policy")
async def get_activity_policy_public(
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityPolicyPublicOut:
    """The policy in force, for the authoring form. Permissive when none is saved."""
    policy = await ActivitiesFacade(db).get_activity_policy()
    return ActivityPolicyPublicOut(
        expose_payload_to_agent_default=policy.expose_payload_to_agent_default,
        expose_payload_to_agent_locked=policy.expose_payload_to_agent_locked,
        echo_includes_content_default=policy.echo_includes_content_default,
        echo_includes_content_locked=policy.echo_includes_content_locked,
        retention_days_default=policy.retention_days_default,
        retention_days_max=policy.retention_days_max,
    )


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
    facade = ActivitiesFacade(db)
    activation = await facade.start_activation(
        project_id=access.project_id,
        chatroom_id=chatroom_id,
        activity_type_id=body.activity_type_id,
        started_by_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    activity_type = await _resolve_activation_type(
        facade, project_id=access.project_id, activation=activation
    )
    await _dispatch_activation_started(activation, activity_type)
    return _activation_out(activation, activity_type)


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
    facade = ActivitiesFacade(db)
    result = await facade.end_activation(
        chatroom_id=chatroom_id,
        activation_id=activation_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    if result.transitioned:
        await dispatch_activation_ended(chatroom_id, result.activation.id)
    activity_type = await _resolve_activation_type(
        facade, project_id=access.project_id, activation=result.activation
    )
    return _activation_out(result.activation, activity_type)


@chatroom_router.get("/{chatroom_id}/activity-activations/active")
async def get_active_activity_activation(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityActivationOut | None:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    facade = ActivitiesFacade(db)
    activation = await facade.get_active_activation(chatroom_id)
    if activation is None:
        return None
    activity_type = await _resolve_activation_type(
        facade, project_id=access.project_id, activation=activation
    )
    return _activation_out(activation, activity_type)


@chatroom_router.patch("/{chatroom_id}/activity-activations/{activation_id}/completion")
async def set_activity_session_completion(
    body: ActivitySessionCompletionIn,
    chatroom_id: uuid.UUID = Path(...),
    activation_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivitySessionOut:
    """A participant declares themselves finished with the running activity, or
    undoes it ([R30.22]).

    Keyed on the activation rather than on a session id: participants no longer
    open sessions, so a client legitimately has no session id to send -- the
    server resolves or creates the one for this round. ``ensure_can_send``
    because this writes; the subject is forced to the caller inside the service
    (the admin arm passes ``caller_user_id=None``, as the session open does).
    """
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    facade = ActivitiesFacade(db)
    result = await facade.set_session_completion(
        project_id=access.project_id,
        chatroom_id=chatroom_id,
        activation_id=activation_id,
        subject_user_id=body.subject_user_id or principal.user_id,
        caller_user_id=None if principal.is_admin else principal.user_id,
        completed=body.completed,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    if result.transitioned:
        await _dispatch_activation_progress(facade, result.activation)
    return _session_out(result.session)


@chatroom_router.get("/{chatroom_id}/activity-activations/{activation_id}/completion")
async def get_activity_session_completion(
    chatroom_id: uuid.UUID = Path(...),
    activation_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivitySessionOut | None:
    """The caller's own session for this round, or ``null`` if they have none.

    The read counterpart of the completion PATCH. Without it a participant who
    reloads cannot know they had already declared themselves finished: they hold
    no session id to ask with, and the panel would render the toggle in the wrong
    state. Creates nothing — looking at the panel is not answering.
    """
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    session = await ActivitiesFacade(db).get_session_for_round(
        project_id=access.project_id,
        chatroom_id=chatroom_id,
        activation_id=activation_id,
        subject_user_id=principal.user_id,
        caller_user_id=None if principal.is_admin else principal.user_id,
    )
    return _session_out(session) if session is not None else None


@chatroom_router.get("/{chatroom_id}/activity-activations/{activation_id}/progress")
async def get_activity_activation_progress(
    chatroom_id: uuid.UUID = Path(...),
    activation_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityActivationProgressOut:
    """How many participants have declared themselves finished ([R30.22]).

    ``ensure_room_creator``, not the send floor: this is the facilitator's view
    of the class, and a participant learning how many peers have finished is a
    different decision nobody has made.
    """
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_room_creator(access, principal=principal)
    completed, in_progress = await ActivitiesFacade(db).count_activation_sessions(
        chatroom_id=chatroom_id, activation_id=activation_id
    )
    return ActivityActivationProgressOut(completed=completed, in_progress=in_progress)


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
    await _dispatch_submission(chatroom_id, submission, signal_payload, db=db)
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
    chatroom_id: uuid.UUID,
    submission: ActivitySubmission,
    signal_payload: dict[str, Any],
    *,
    db: AsyncSession,
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
    # Re-arm the silence clock ([R15.02]): a submission is room activity, so an agent
    # on `silence_minutes` must not read a class busy filling in a worksheet as a
    # lull. Deliberately NOT a full wake-up evaluation — that would wake every
    # `every_n_messages` agent once per submission; see `triggers.evaluate_room_activity`.
    # Ordered last on purpose: it is the only step here that costs a DB read, and it is
    # best-effort, so neither the client's realtime emit nor the two enqueues should
    # queue behind it. Matters under load — a class submitting together would otherwise
    # push every validation job back by one SELECT each.
    try:
        await ConversationFacade(db).note_room_activity(chatroom_id=chatroom_id)
    except Exception:
        _log.warning("silence-clock re-arm failed for activity submission %s", submission.id, exc_info=True)
    # A submission moves the facilitator's counts in two ways and neither used to
    # be broadcast: the first one opens the subject's session (in_progress 0 -> 1),
    # and any submission retracts an "I am finished" declaration
    # (``SubmissionService.submit``), which moves a participant back out of the
    # completed column. Without this the panel would keep showing a class as
    # finished while it carried on working -- and the count is what a facilitator
    # decides to move on from. Unconditional rather than gated on "did anything
    # change": the route cannot know without asking, and asking IS the read.
    await _dispatch_room_activation_progress(ActivitiesFacade(db), chatroom_id)


async def _dispatch_activation_started(
    activation: ActivityActivation, activity_type: ActivityType | None
) -> None:
    try:
        payload: dict[str, Any] = {
            "activation_id": str(activation.id),
            "activity_type_id": str(activation.activity_type_id),
            "started_by": str(activation.started_by_user_id),
        }
        if activity_type is not None:
            # Same participant projection as the HTTP reads (R30.26) — no
            # validator_config on any realtime payload.
            payload["activity_type"] = _type_public_out(activity_type).model_dump(mode="json")
        await Publisher(room_channel(activation.chatroom_id)).emit("activity.activation.started", payload)
    except Exception:
        _log.error("realtime publish failed for activity activation %s", activation.id, exc_info=True)


async def _dispatch_room_activation_progress(facade: ActivitiesFacade, chatroom_id: uuid.UUID) -> None:
    """As :func:`_dispatch_activation_progress`, for a caller holding the room
    rather than the round.

    The submit path is the case: it has committed by the time it reaches here, so
    re-reading the active activation is both cheaper and less fragile than
    threading it out through ``submit``'s return. ``None`` means the facilitator
    ended the round in between, which is exactly when there is nothing to report.
    """
    try:
        activation = await facade.get_active_activation(chatroom_id)
    except Exception:
        _log.warning("activity progress lookup failed for room %s", chatroom_id, exc_info=True)
        return
    if activation is None:
        return
    await _dispatch_activation_progress(facade, activation)


async def _dispatch_activation_progress(facade: ActivitiesFacade, activation: ActivityActivation) -> None:
    """Tell the facilitator who started this round that its progress moved.

    Addressed to that one user's channel, never the room's ([R28.13] does the
    same for observations): the counts are the facilitator's view of the class,
    and in a two-person group "1 finished" identifies the other participant.

    Post-commit and best-effort in both halves -- the count read as much as the
    publish. The write is already durable, so neither a stale count nor a Redis
    hiccup may surface as a failed request.

    A dropped event does NOT self-heal for the starter: they are the only viewer
    this targets and they have no poll (``useActivationProgress`` polls only the
    viewers who cannot receive it), so they hold a stale count until the next
    event or a remount. That is the accepted cost of keeping per-round counts off
    the room channel -- and the reason every writer that moves the counts has to
    call this, the submit path included.
    """
    try:
        completed, in_progress = await facade.count_activation_sessions(
            chatroom_id=activation.chatroom_id, activation_id=activation.id
        )
        await Publisher(user_channel(activation.started_by_user_id)).emit(
            "activity.session.completion",
            {
                "chatroom_id": str(activation.chatroom_id),
                "activation_id": str(activation.id),
                "completed": completed,
                "in_progress": in_progress,
            },
        )
    except Exception:
        _log.warning("activity progress publish failed for activation %s", activation.id, exc_info=True)


async def dispatch_activation_ended(chatroom_id: uuid.UUID, activation_id: uuid.UUID) -> None:
    """Tell one room its activation ended. Public because three routes across two
    modules end activations — the owner delete and the project opt-out here, and
    the admin delete in ``admin_activities`` — and each must broadcast the same
    event after its own commit."""
    try:
        await Publisher(room_channel(chatroom_id)).emit(
            "activity.activation.ended", {"activation_id": str(activation_id)}
        )
    except Exception:
        _log.error("realtime publish failed for ended activity activation %s", activation_id, exc_info=True)


__all__ = [
    "chatroom_router",
    "dispatch_activation_ended",
    "project_router",
    "validator_router",
]
