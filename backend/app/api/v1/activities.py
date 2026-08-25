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
    GroupProposalResolution,
    GroupProposalTally,
    ProposalStatus,
    ValidatorKind,
    VoteChoice,
)
from contexts.activities.interfaces.broadcast import (
    InitiatingAgent,
    activity_type_public_payload,
    dispatch_activation_ended,
    dispatch_activation_progress,
    dispatch_activation_started,
    dispatch_group_proposal,
    dispatch_room_activation_progress,
)
from contexts.activities.interfaces.facade import ActivitiesFacade
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.conversation.domain.models import ChatroomAgentRole
from contexts.conversation.interfaces import room_channel
from contexts.conversation.interfaces.access import (
    ensure_can_read,
    ensure_can_send,
    ensure_room_creator,
    is_room_creator,
    resolve_room_access,
)
from contexts.conversation.interfaces.facade import ConversationFacade
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
    # The consent fraction a group must clear ([R30.40]); null means the type is
    # individual-only, which is every type that predates 0081. Shape-checked in
    # the service, not here: it is a domain rule and the platform-install path
    # has to run the same one.
    group_config: dict[str, Any] | None = None


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
    # Editable here and nowhere else. Without it the field would be settable only
    # by hand-editing the shipped catalogue JSON, and no project could ever
    # declare its own type group-submittable — see §6 of the dossier for why
    # `AdminPlatformActivityTypeIn` deliberately does NOT gain it.
    group_config: dict[str, Any] | None = None


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
    # Null for an individual-only type. Unlike `validator_config` this is NOT
    # owner-confidential: the people being asked to vote have to see the bar they
    # are voting against ([R30.40]), which is why it is a column of its own.
    group_config: dict[str, Any] | None = None


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
    # Set only when a delegated agent started this round **and may be named to this
    # reader** ([R30.37]); `started_by_user_id` above still names the teacher whose
    # authority it acted on. Both are withheld for an observer-started round, since
    # this read is not creator-gated and an observer is invisible to non-creators
    # ([R28.10]) — see `_resolve_initiating_agent`. The name is resolved through the
    # agents facade, never joined ([R30.09]), and is `None` when the agent has since
    # been deleted.
    started_by_agent_id: uuid.UUID | None = None
    started_by_agent_name: str | None = None


class ActivitySessionOut(BaseModel):
    id: uuid.UUID
    activity_type_id: uuid.UUID
    chatroom_id: uuid.UUID
    # Exactly one of the two subject fields is set, and `subject_kind` says
    # which ([R30.39]). A client must branch on the kind rather than on which id
    # is null: the pair is the subject, and reading `subject_user_id` alone on a
    # group session yields null with no explanation.
    subject_user_id: uuid.UUID | None
    subject_member_group_id: uuid.UUID | None = None
    subject_kind: str = "user"
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


class ActivityGroupProposalIn(BaseModel):
    member_group_id: uuid.UUID
    activity_type_id: uuid.UUID
    payload: dict[str, Any]


class ActivityGroupVoteIn(BaseModel):
    approve: bool


class ActivityGroupVoteOut(BaseModel):
    """One pinned voter's decision ([R30.42]).

    Only ever populated for a caller entitled to the per-person record: the
    proposal's pinned voters and the room creator. Every other reader gets the
    counts and an empty list — and no agent reaches this surface at all.
    """

    user_id: uuid.UUID
    approve: bool
    created_at: str | None


class ActivityGroupProposalOut(BaseModel):
    """A group's proposal and where its vote stands.

    ``payload`` is here because the people reading this are the ones being asked
    to approve it; it is NOT on the room broadcast, which carries counts only
    (AC-11). The two surfaces have different audiences and deliberately different
    contents.
    """

    id: uuid.UUID
    chatroom_id: uuid.UUID
    activation_id: uuid.UUID
    activity_type_id: uuid.UUID
    member_group_id: uuid.UUID
    proposer_user_id: uuid.UUID
    payload: dict[str, Any]
    status: str
    required_approvals: int
    approvals: int
    rejections: int
    undecided: int
    voter_count: int
    votes: list[ActivityGroupVoteOut]
    created_at: str | None
    expires_at: str | None
    resolved_at: str | None
    submission_id: uuid.UUID | None


class ActivityGroupProposalsOut(BaseModel):
    items: list[ActivityGroupProposalOut]


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
        group_config=t.group_config,
    )


def _type_public_out(t: ActivityType) -> ActivityTypePublicOut:
    """The HTTP face of the participant rendering contract ([R30.26]).

    Built **from** ``activity_type_public_payload`` rather than beside it, so this
    response and the room broadcast that carries the same contract cannot drift:
    a field added to one and not the other is a validation error here, not a
    client that silently stops receiving it.
    """
    return ActivityTypePublicOut(**activity_type_public_payload(t))


def _session_out(s: ActivitySession) -> ActivitySessionOut:
    return ActivitySessionOut(
        id=s.id,
        activity_type_id=s.activity_type_id,
        chatroom_id=s.chatroom_id,
        subject_user_id=s.subject_user_id,
        subject_member_group_id=s.subject_member_group_id,
        subject_kind=s.subject_kind.value,
        activation_id=s.activation_id,
        status=s.status.value,
        created_at=s.created_at.isoformat() if s.created_at else None,
        closed_at=s.closed_at.isoformat() if s.closed_at else None,
        completed_at=s.completed_at.isoformat() if s.completed_at else None,
    )


def _proposal_out(tally: GroupProposalTally) -> ActivityGroupProposalOut:
    """The HTTP face of a proposal, votes included only when the tally has them.

    The entitlement decision is made once, in the service, and expressed as an
    empty ``votes`` tuple. This function does not re-derive it: two places
    deciding who may see a vote record is how one of them ends up wrong.
    """
    p = tally.proposal
    return ActivityGroupProposalOut(
        id=p.id,
        chatroom_id=p.chatroom_id,
        activation_id=p.activation_id,
        activity_type_id=p.activity_type_id,
        member_group_id=p.member_group_id,
        proposer_user_id=p.proposer_user_id,
        payload=p.payload,
        status=p.status.value,
        required_approvals=p.required_approvals,
        approvals=tally.approvals,
        rejections=tally.rejections,
        undecided=tally.undecided,
        voter_count=len(p.voter_user_ids),
        votes=[
            ActivityGroupVoteOut(
                user_id=v.user_id,
                approve=v.choice is VoteChoice.APPROVE,
                created_at=v.created_at.isoformat() if v.created_at else None,
            )
            for v in tally.votes
        ],
        created_at=p.created_at.isoformat() if p.created_at else None,
        expires_at=p.expires_at.isoformat() if p.expires_at else None,
        resolved_at=p.resolved_at.isoformat() if p.resolved_at else None,
        submission_id=p.submission_id,
    )


def _activation_out(
    a: ActivityActivation,
    activity_type: ActivityType | None,
    *,
    initiating_agent: InitiatingAgent | None = None,
) -> ActivityActivationOut:
    return ActivityActivationOut(
        id=a.id,
        chatroom_id=a.chatroom_id,
        activity_type_id=a.activity_type_id,
        started_by_user_id=a.started_by_user_id,
        status=a.status.value,
        created_at=a.created_at.isoformat() if a.created_at else None,
        ended_at=a.ended_at.isoformat() if a.ended_at else None,
        activity_type=_type_public_out(activity_type) if activity_type is not None else None,
        # Both fields ride the one disclosure decision the caller made; see
        # `_resolve_initiating_agent`. Never read straight off the activation row —
        # the row records who started it, which is a different question from who
        # this reader may be told about.
        started_by_agent_id=initiating_agent.agent_id if initiating_agent else None,
        started_by_agent_name=initiating_agent.name if initiating_agent else None,
    )


async def _resolve_initiating_agent(
    db: AsyncSession, activation: ActivityActivation
) -> InitiatingAgent | None:
    """The agent to name as this round's initiator, or ``None`` ([R30.37]).

    ``None`` for a human-started round, and also for one started by an **observer**:
    an observer binding is withheld from every non-creator ([R28.02], [R28.10]) and
    the agent sends no messages, so this read is reachable by any room member —
    guests included — and would be the single channel that outs it. The room-scoped
    reads here are not creator-gated, so the suppression has to happen at the value,
    not at the caller.

    Two bounded facade reads, both skipped entirely for the overwhelmingly common
    human-started round, and neither a join ([R30.09], [R30.31]). Degrades to
    ``None`` rather than propagating: a lookup failing must not turn a working
    activation read into a 500, and withholding is the safe direction.
    """
    if activation.started_by_agent_id is None:
        return None
    agent_id = activation.started_by_agent_id
    try:
        role = await ConversationFacade(db).agent_role_in_chatroom(
            chatroom_id=activation.chatroom_id, agent_id=agent_id
        )
        if role is ChatroomAgentRole.OBSERVER:
            return None
        names = await AgentsFacade(db).agent_names([agent_id])
    except Exception:
        _log.warning("initiating agent resolution failed for activation %s", activation.id, exc_info=True)
        return None
    # An unbound agent (`role is None`) was unbound after starting the round. It is
    # named: it was a normal agent when it acted, its messages are still in the
    # transcript under its name, and dropping the attribution would make a round
    # nobody can account for.
    return InitiatingAgent(agent_id=agent_id, name=names.get(agent_id))


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
        group_config=body.group_config,
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
        group_config=body.group_config,
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
    # `None` on this path by construction: the HTTP start is a human's, so the
    # lookup short-circuits. Threaded anyway so the broadcast and the response
    # carry the same attribution whichever path produced the activation.
    initiating_agent = await _resolve_initiating_agent(db, activation)
    await dispatch_activation_started(activation, activity_type, initiating_agent=initiating_agent)
    return _activation_out(activation, activity_type, initiating_agent=initiating_agent)


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
    return _activation_out(
        result.activation,
        activity_type,
        initiating_agent=await _resolve_initiating_agent(db, result.activation),
    )


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
    return _activation_out(
        activation,
        activity_type,
        initiating_agent=await _resolve_initiating_agent(db, activation),
    )


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
        await dispatch_activation_progress(facade, result.activation)
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


# --------------------------------------------------------------------------- #
# Group proposals (room-scoped) — [R30.41], [R30.42]                           #
# --------------------------------------------------------------------------- #


@chatroom_router.post("/{chatroom_id}/activity-proposals")
async def create_activity_group_proposal(
    body: ActivityGroupProposalIn,
    chatroom_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityGroupProposalOut:
    """Propose this group's answer to the live round (AC-5).

    ``ensure_can_send``, not ``ensure_can_read``: proposing is the first half of
    submitting, and a reader who may not answer may not start a vote that would
    answer for them either. The group gates are the service's — this route knows
    nothing about groups beyond forwarding the id the caller named.

    Creating one can also settle it, when the fraction over the pinned set rounds
    down to the proposer's own approval. The post-commit fan-out is therefore the
    vote route's, not a shorter version of it.
    """
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    resolution = await ActivitiesFacade(db).create_group_proposal(
        project_id=access.project_id,
        chatroom_id=chatroom_id,
        member_group_id=body.member_group_id,
        activity_type_id=body.activity_type_id,
        proposer_user_id=principal.user_id,
        payload=body.payload,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    await _dispatch_proposal(chatroom_id, resolution, opened=True, db=db)
    return _proposal_out(resolution.tally)


@chatroom_router.post("/{chatroom_id}/activity-proposals/{proposal_id}/votes")
async def vote_on_activity_group_proposal(
    body: ActivityGroupVoteIn,
    chatroom_id: uuid.UUID = Path(...),
    proposal_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityGroupProposalOut:
    """Record this caller's vote, and submit if it carries the proposal.

    Everything after the commit is the submit path's own post-commit fan-out,
    reached with the submission an acceptance produced — so a group submission
    reaches the room, the validation worker and the reactive rules by exactly the
    routes an individual one does.
    """
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    resolution = await ActivitiesFacade(db).vote_on_group_proposal(
        project_id=access.project_id,
        chatroom_id=chatroom_id,
        proposal_id=proposal_id,
        voter_user_id=principal.user_id,
        approve=body.approve,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    await _dispatch_proposal(chatroom_id, resolution, opened=False, db=db)
    return _proposal_out(resolution.tally)


async def _dispatch_proposal(
    chatroom_id: uuid.UUID,
    resolution: GroupProposalResolution,
    *,
    opened: bool,
    db: AsyncSession,
) -> None:
    """Post-commit fan-out shared by the create and vote routes.

    The event name is keyed on the proposal's STATUS, not on whether this request
    is the one that moved it. A vote that lost the resolve race still finds the
    proposal decided, and announcing that as `voted` would tell a client the vote
    is still running while the payload beside it says otherwise.

    ``opened`` distinguishes only the still-open case: a creation that has not
    settled is `opened`, a vote that has not settled is `voted`, and either that
    HAS settled is `resolved` — because a proposal accepted the instant it was
    created is not an opening, whatever route produced it.

    A submission goes through the individual path's own fan-out, so a group
    submission reaches the room, the validation worker and the reactive rules by
    exactly the routes an individual one does."""
    if resolution.tally.proposal.status is not ProposalStatus.OPEN:
        event = "activity.proposal.resolved"
    else:
        event = "activity.proposal.opened" if opened else "activity.proposal.voted"
    await dispatch_group_proposal(event, resolution.tally)
    if resolution.submission is not None and resolution.signal_payload is not None:
        await _dispatch_submission(chatroom_id, resolution.submission, resolution.signal_payload, db=db)


@chatroom_router.post("/{chatroom_id}/activity-proposals/{proposal_id}/withdraw")
async def withdraw_activity_group_proposal(
    chatroom_id: uuid.UUID = Path(...),
    proposal_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityGroupProposalOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_send(access, is_admin=principal.is_admin)
    tally = await ActivitiesFacade(db).withdraw_group_proposal(
        chatroom_id=chatroom_id,
        proposal_id=proposal_id,
        caller_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    await dispatch_group_proposal("activity.proposal.resolved", tally)
    return _proposal_out(tally)


@chatroom_router.get("/{chatroom_id}/activity-proposals")
async def list_activity_group_proposals(
    chatroom_id: uuid.UUID = Path(...),
    activation_id: uuid.UUID = Query(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityGroupProposalsOut:
    """The live proposals this caller may see for one round (AC-12).

    Room access is necessary and not sufficient: the service narrows to the
    caller's own bound groups, or to every bound group for the room creator. A
    room member in no group sees an empty list rather than a 403 — there is
    nothing being withheld from them, there is simply nothing of theirs.
    """
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    tallies = await ActivitiesFacade(db).list_group_proposals(
        project_id=access.project_id,
        chatroom_id=chatroom_id,
        activation_id=activation_id,
        caller_user_id=principal.user_id,
        caller_is_room_creator=is_room_creator(access, principal=principal),
    )
    return ActivityGroupProposalsOut(items=[_proposal_out(t) for t in tallies])


@chatroom_router.get("/{chatroom_id}/activity-proposals/{proposal_id}")
async def get_activity_group_proposal(
    chatroom_id: uuid.UUID = Path(...),
    proposal_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityGroupProposalOut:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    tally = await ActivitiesFacade(db).get_group_proposal(
        chatroom_id=chatroom_id,
        proposal_id=proposal_id,
        caller_user_id=principal.user_id,
        caller_is_room_creator=is_room_creator(access, principal=principal),
    )
    return _proposal_out(tally)


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
    await dispatch_room_activation_progress(ActivitiesFacade(db), chatroom_id)


__all__ = [
    "chatroom_router",
    "dispatch_activation_ended",
    "project_router",
    "validator_router",
]
