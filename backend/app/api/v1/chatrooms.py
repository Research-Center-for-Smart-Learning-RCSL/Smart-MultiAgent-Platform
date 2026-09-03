"""`/api/workspaces/{id}/chatrooms` + `/api/chatrooms/*` — F.2 / §22.10."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams, require_if_match
from app.api.v1.orchestration import ApprovalWithVotesOut, approval_with_votes_out
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.conversation.application.access import (
    ensure_can_read,
    ensure_room_creator,
    is_moderator_roles,
    is_room_creator,
    resolve_room_access,
)
from contexts.conversation.application.chatroom_service import (
    ChatroomFlagsPatch,
    ChatroomService,
)
from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    WorkspaceNotFound,
)
from contexts.conversation.domain.models import ChatroomAgentRole
from contexts.conversation.interfaces import room_channel
from contexts.conversation.interfaces.author_labels import prefer_guest_label
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.identity.interfaces.facade import IdentityFacade
from contexts.orchestration.interfaces.facade import OrchestrationFacade
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    _raise_forbidden,
    current_context,
    current_principal,
    get_role_resolver,
)
from shared_kernel.auth.permissions import (
    Capability,
    Principal,
    Role,
    Scope,
    decide,
)
from shared_kernel.db.session import db_session
from shared_kernel.realtime.pubsub import Publisher

workspace_router = APIRouter(prefix="/api/workspaces", tags=["chatrooms"])
chatroom_router = APIRouter(prefix="/api/chatrooms", tags=["chatrooms"])

# Ceiling on one room's delegated activity allowlist ([R30.37]). See
# `AgentActivityControlIn.activity_type_ids` for why this is bounded at all.
_MAX_ACTIVITY_ALLOWLIST = 100

# Same reasoning for a room's Member Group bindings (§13.2a). A classroom binds
# one or a handful; a request naming hundreds is a mistake or an attack, and
# either way the write should be refused rather than performed.
_MAX_BOUND_GROUPS = 50

#: The room flags that are the **creator's** call rather than any
#: `RESOURCE_CREATE_EDIT` holder's ([R28.09], [R32.05]). Named once because
#: `patch_chatroom` consults it twice, in two different ways — as the "this patch
#: needs no capability at all" test and as the per-field creator gate — and the two
#: drifting apart is how a disclosure flag ends up demanding the wrong permission.
_DISCLOSURE_FIELDS: frozenset[str] = frozenset({"disclose_observers", "disclose_drafts"})


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class ChatroomCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    allow_org_members: bool = False
    allow_project_members: bool = True
    allow_project_owners_only: bool = False
    allow_guest_links: bool = False
    # §13.2a. Mutually exclusive with allow_project_members (R13.04); the service
    # refuses the pair with 422 rather than silently correcting either one.
    allow_member_groups: bool = False


class ChatroomPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    allow_org_members: bool | None = None
    allow_project_members: bool | None = None
    allow_project_owners_only: bool | None = None
    allow_guest_links: bool | None = None
    allow_member_groups: bool | None = None
    # R28.09 — creator-only (per-field gate in the handler; the capability
    # check above it covers the other fields).
    disclose_observers: bool | None = None
    # R32.05 — creator-only on exactly the same terms. Listed in
    # `_DISCLOSURE_FIELDS` below so the "disclosure-only patch needs no
    # RESOURCE_CREATE_EDIT" carve-out covers it without a second branch.
    disclose_drafts: bool | None = None


class ChatroomOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    allow_org_members: bool
    allow_project_members: bool
    allow_project_owners_only: bool
    allow_guest_links: bool
    allow_member_groups: bool
    version: int
    created_at: str
    deleted_at: str | None
    created_by_user_id: uuid.UUID | None
    disclose_observers: bool
    # "You are notified that observers are enabled" — false whenever
    # disclosure is off, regardless of actual bindings (R28.09).
    observers_present: bool
    # §32. The same pair, for the same reason ([R32.05]): `disclose_drafts` is the
    # room's setting and `drafts_readable` is what a participant is actually told,
    # so it is false whenever disclosure is off regardless of any live grant. Which
    # agent holds the grant is never disclosed to a non-creator either way.
    disclose_drafts: bool
    drafts_readable: bool
    # Advisory only (R5.05): lets the client hide guest-forbidden controls
    # (export, settings, agent binding — docs/UI/07-conversation.md) rather than
    # offer a control that 403s. A pure guest holds a guest link but no
    # org/project role; every enforcement is still server-side.
    viewer_is_guest: bool = False
    # V-4 (R13.21/R13.23): may this viewer edit and delete other people's
    # messages here? Serialized because the client cannot re-derive it —
    # PROJECT_OWNER is granted to any org owner of the parent org with no
    # `project_members` row (R5.03), which no members-list lookup can see.
    is_moderator: bool = False


class GuestLinkOut(BaseModel):
    url: str
    chatroom_id: uuid.UUID
    guest_token: str


class AgentRef(BaseModel):
    agent_id: uuid.UUID
    # Response-side: populated only for the room creator (R28.10); the field
    # is dropped from serialization when None. Request-side (POST body):
    # optional, defaults to a normal binding.
    role: Literal["normal", "observer"] | None = None
    # Delegated activity control ([R30.37]), creator-only for exactly the reason
    # `role` is: a non-creator must not learn the room's delegation layout any more
    # than it learns its observer layout ([R28.10]). `None` means "you are not
    # told" and is dropped from serialization.
    #
    # **Response-side only.** This model doubles as the POST body, and
    # `add_chatroom_agent` reads neither field — granting is its own route, because
    # it is a different authority decision with a different gate. A bind that sends
    # them succeeds and grants nothing. Splitting the request model from the
    # response model is FU-6 of the delegated-activity-control dossier.
    may_control_activities: bool | None = None
    activity_type_allowlist: list[uuid.UUID] | None = None
    # Live draft reading ([R32.03]), creator-only for the same reason and on the
    # same response-only terms as the two fields above. A bind that sends it
    # succeeds and grants nothing; granting is its own route.
    may_read_drafts: bool | None = None


class AgentRolePatchIn(BaseModel):
    role: Literal["normal", "observer"]


class AgentDraftAccessIn(BaseModel):
    """Grant or revoke one bound agent's live-draft reading ([R32.03]).

    One field, deliberately. Its sibling ``AgentActivityControlIn`` carries an
    allowlist because activity control is authority over a *set* of worksheets the
    teacher chooses; draft reading has no such set. What a granted agent may
    actually read is decided per call by the activity type's own
    ``expose_payload_to_agent`` and by the platform payload policy ([R32.04]) — the
    same two gates a submitted payload passes. A list here would be a third gate to
    keep in step with those two, and the state where they disagreed would be a draft
    readable on looser terms than its own submission.
    """

    granted: bool


class AgentActivityControlIn(BaseModel):
    """Grant or revoke one bound agent's activity start/end authority ([R30.37]).

    ``activity_type_ids`` is required whenever ``granted`` is true and is validated
    against the room's project before anything is written — an unresolvable id is a
    422, never a silently dropped entry. On a revoke it is ignored: the stored
    allowlist is left in place so the teacher's selection survives a re-grant.
    """

    granted: bool
    # Bounded because every id costs a reachability query here AND another on
    # every turn of the granted agent (`activity_tools._resolve_allowed_types`),
    # so an oversized list is not a one-off cost but a permanent per-turn one on
    # an agent that may wake on every message. A project's type count is small;
    # this is a ceiling, not a working limit.
    activity_type_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_ACTIVITY_ALLOWLIST)


class ChatroomMemberOut(BaseModel):
    user_id: uuid.UUID
    display_name: str | None


def _to_out(
    r,
    *,
    has_observers: bool = False,
    has_draft_readers: bool = False,
    viewer_is_pure_guest: bool = False,
    is_moderator: bool = False,
) -> ChatroomOut:
    # O-8 (R28.02): guests are denied every observer surface — a pure guest
    # (guest link only, no project role) receives fail-closed neutral values,
    # indistinguishable from a room with disclosure off and no creator on
    # record, so the DTO is not an observer-existence oracle. `is_moderator`
    # is neutralised on the same path and for the same reason: a guest is
    # never a moderator, and the field must not become an oracle either.
    return ChatroomOut(
        id=r.id,
        workspace_id=r.workspace_id,
        name=r.name,
        allow_org_members=r.allow_org_members,
        allow_project_members=r.allow_project_members,
        allow_project_owners_only=r.allow_project_owners_only,
        allow_guest_links=r.allow_guest_links,
        allow_member_groups=r.allow_member_groups,
        version=r.version,
        created_at=r.created_at.isoformat(),
        deleted_at=r.deleted_at.isoformat() if r.deleted_at else None,
        created_by_user_id=None if viewer_is_pure_guest else r.created_by_user_id,
        disclose_observers=False if viewer_is_pure_guest else r.disclose_observers,
        observers_present=bool(not viewer_is_pure_guest and r.disclose_observers and has_observers),
        # §32's neutral value for a guest is the same one every non-creator gets:
        # `drafts_readable` is already false whenever disclosure is off, for
        # everyone. There is deliberately no *extra* guest suppression of the kind
        # `disclose_observers` carries above, and the two cases differ for a reason.
        # An observer is a surface a guest is denied entirely, so a neutral value
        # there hides something the guest has no stake in. A guest's own unsent text
        # is read on exactly the same terms as a member's (§8), so suppressing the
        # chip would withhold the disclosure from the person it is about. Either way
        # a guest learns nothing about *which* agent holds the grant ([R32.05]).
        disclose_drafts=r.disclose_drafts,
        drafts_readable=bool(r.disclose_drafts and has_draft_readers),
        viewer_is_guest=viewer_is_pure_guest,
        is_moderator=bool(not viewer_is_pure_guest and is_moderator),
    )


async def _project_id_for_workspace(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> uuid.UUID:
    ws = await ConversationFacade(db).get_workspace(workspace_id)
    if ws is None:
        raise WorkspaceNotFound(str(workspace_id))
    return ws.project_id


async def _project_id_for_chatroom(
    db: AsyncSession,
    chatroom_id: uuid.UUID,
) -> uuid.UUID:
    facade = ConversationFacade(db)
    room = await facade.get_chatroom(chatroom_id)
    if room is None:
        raise ChatroomNotFound(str(chatroom_id))
    ws = await facade.get_workspace(room.workspace_id)
    if ws is None:
        raise WorkspaceNotFound(str(room.workspace_id))
    return ws.project_id


async def _emit_chatroom_updated(db: AsyncSession, chatroom_id: uuid.UUID) -> None:
    """Tell every live viewer of this room to re-read its DTO ([R28.09]).

    **The payload is the room id and nothing else, and that is a security
    constraint rather than a style choice.** The room channel has no
    per-recipient filtering — ``_pubsub_fanin`` in
    ``shared_kernel/realtime/connection.py`` hands every frame to every
    subscriber, guests included — so anything carried here is disclosed to
    people the DTO deliberately lies to. ``_to_out`` neutralises
    ``observers_present``, ``disclose_observers``, ``created_by_user_id`` and
    ``is_moderator`` for a pure guest, and it can only do that because it runs
    once per viewer. An ids-only "refetch me" keeps that property: each client
    re-GETs and gets its own answer.

    **Commits first.** ``db_session`` owns the transaction and commits after the
    handler returns, so a frame published from the handler body would announce a
    write a later rollback could still undo — every viewer would then pay a GET
    to re-read the old value. The dependency's trailing commit is a no-op on the
    now-clean session (see ``shared_kernel/db/session.py``).

    Transport failure is swallowed. The write is already durable at this point;
    raising here would return 500 for a change that happened, which is a worse
    lie than a missed refresh — and the next reload reconciles. Same posture as
    ``emit_agent_finished_error``.
    """
    await db.commit()
    try:
        await Publisher(room_channel(chatroom_id)).emit("chatroom.updated", {"chatroom_id": str(chatroom_id)})
    except Exception:
        # `opt(exception=True)` rather than a bare warning: this is the only
        # record that a viewer's refresh was lost, and "emit failed" with no
        # cause cannot be acted on.
        logger.bind(room_id=str(chatroom_id)).opt(exception=True).warning("chatroom.updated emit failed")


async def _require_project_cap(
    db: AsyncSession,
    principal: Principal,
    project_id: uuid.UUID,
    capability: Capability,
) -> None:
    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        capability,
        Scope(project_id=project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)


# --------------------------------------------------------------------------- #
# List + create under workspace
# --------------------------------------------------------------------------- #


@workspace_router.get("/{workspace_id}/chatrooms")
async def list_chatrooms(
    workspace_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[ChatroomOut]:
    project_id = await _project_id_for_workspace(db, workspace_id)
    # Holding a role in the parent project is the price of admission to the
    # listing at all; which rooms it then contains is the room ACL's answer, not
    # this check's. Admin bypasses both.
    moderator = principal.is_admin
    if not principal.is_admin:
        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(
            principal,
            Scope(project_id=project_id),
        )
        if not roles:
            _raise_forbidden("caller is not a member of the project")
        moderator = is_moderator_roles(roles)
    # R13.32 — enumeration follows confidentiality. This listing used to return
    # every live room in the workspace to anyone holding any role in the parent
    # project, which for an org-owned project is every member of the org
    # (R5.03). Names, all four access flags and observer presence leaked for
    # rooms the same caller was refused on open.
    #
    # Filtering happens before pagination, so `offset` counts visible rooms.
    visible = await ConversationFacade(db).visible_rooms_in_workspace(
        principal=principal,
        workspace_id=workspace_id,
    )
    rows = visible[pagination.offset : pagination.offset + pagination.limit]
    service = ChatroomService(db)
    room_ids = [r.id for r in rows]
    with_observers = await service.rooms_with_observers(room_ids)
    with_draft_readers = await service.rooms_with_draft_readers(room_ids)
    return [
        _to_out(
            r,
            has_observers=r.id in with_observers,
            has_draft_readers=r.id in with_draft_readers,
            is_moderator=moderator,
        )
        for r in rows
    ]


@workspace_router.post(
    "/{workspace_id}/chatrooms",
    status_code=status.HTTP_201_CREATED,
)
async def create_chatroom(
    body: ChatroomCreateIn,
    workspace_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ChatroomOut:
    project_id = await _project_id_for_workspace(db, workspace_id)
    await _require_project_cap(db, principal, project_id, Capability.CHAT_CREATE)
    service = ChatroomService(db)
    room = await service.create(
        workspace_id=workspace_id,
        name=body.name,
        allow_org_members=body.allow_org_members,
        allow_project_members=body.allow_project_members,
        allow_project_owners_only=body.allow_project_owners_only,
        allow_guest_links=body.allow_guest_links,
        allow_member_groups=body.allow_member_groups,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    # CHAT_CREATE is granted to exactly ORG_OWNER and PROJECT_OWNER
    # (permissions.py `_MATRIX`), which is precisely `is_moderator_roles`'
    # predicate — so clearing the gate above already proves the caller moderates
    # this room, and no second role lookup is needed. Without this the 201 body
    # said `is_moderator: false` while a GET one request later said true.
    return _to_out(room, is_moderator=True)


# --------------------------------------------------------------------------- #
# Chatroom-scoped routes
# --------------------------------------------------------------------------- #


@chatroom_router.get("/{chatroom_id}")
async def read_chatroom(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ChatroomOut:
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    pure_guest = False
    moderator = principal.is_admin
    if not principal.is_admin:
        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(
            principal,
            Scope(project_id=project_id, chatroom_id=chatroom_id),
        )
        is_guest = await ConversationFacade(db).is_chatroom_guest(
            chatroom_id=chatroom_id,
            user_id=principal.user_id,
        )
        if not roles and not is_guest:
            _raise_forbidden("not a participant of this room")
        pure_guest = not roles and is_guest
        moderator = is_moderator_roles(roles)
    service = ChatroomService(db)
    room = await service.get(chatroom_id)
    with_observers = await service.rooms_with_observers([chatroom_id])
    with_draft_readers = await service.rooms_with_draft_readers([chatroom_id])
    return _to_out(
        room,
        has_observers=chatroom_id in with_observers,
        has_draft_readers=chatroom_id in with_draft_readers,
        viewer_is_pure_guest=pure_guest,
        is_moderator=moderator,
    )


@chatroom_router.patch("/{chatroom_id}")
async def patch_chatroom(
    body: ChatroomPatchIn,
    chatroom_id: uuid.UUID = Path(...),
    if_match: str = Header(..., alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ChatroomOut:
    fields = set(body.model_dump(exclude_unset=True))
    # Collected for the response DTO as well as the gates: the caller's role
    # set here is whatever the branch below already had to resolve, so only
    # the plain-flags path pays for an extra lookup.
    roles: frozenset[Role]
    if fields and fields <= _DISCLOSURE_FIELDS:
        # O-6 (R28.09) / [R32.05]: a disclosure-only patch is the creator's call
        # and must not additionally demand RESOURCE_CREATE_EDIT — a creator
        # demoted below project owner keeps control of their room's disclosures.
        # A **subset** test rather than an exact-set one, so a patch naming either
        # disclosure flag, or both, takes this path; the earlier exact form would
        # have silently sent a `disclose_drafts`-only patch down the capability
        # branch and demanded a permission [R32.05] says it must not.
        access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
        ensure_room_creator(access, principal=principal)
        roles = access.roles
    else:
        project_id = await _project_id_for_chatroom(db, chatroom_id)
        await _require_project_cap(
            db,
            principal,
            project_id,
            Capability.RESOURCE_CREATE_EDIT,
        )
        if fields & _DISCLOSURE_FIELDS:
            # R28.09 / R32.05: disclosure is the creator's call, not any
            # RESOURCE_CREATE_EDIT holder's — per-field gate on top of the
            # capability check above.
            access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
            ensure_room_creator(access, principal=principal)
            roles = access.roles
        elif principal.is_admin:
            # The bypass below decides the answer, and the settings form now
            # sends one PATCH per toggle — no reason to pay for a resolution
            # whose result cannot change it.
            roles = frozenset()
        else:
            resolver = await get_role_resolver(db)
            roles = await resolver.roles_for(principal, Scope(project_id=project_id))
    moderator = principal.is_admin or is_moderator_roles(roles)
    expected = require_if_match(if_match)
    service = ChatroomService(db)
    room = await service.patch(
        chatroom_id=chatroom_id,
        expected_version=expected,
        patch=ChatroomFlagsPatch(**body.model_dump(exclude_unset=True)),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    with_observers = await service.rooms_with_observers([chatroom_id])
    with_draft_readers = await service.rooms_with_draft_readers([chatroom_id])
    if fields & _DISCLOSURE_FIELDS:
        # F-1, scoped to the disclosure path per §7.2 of the dossier: flipping
        # `disclose_observers` flips `observers_present` for every viewer. A
        # rename going stale on a live viewer is a real but separate defect and
        # is recorded as that dossier's FU-7 rather than widened into here.
        #
        # After `_to_out`'s inputs are read, not before: the commit inside the
        # emit ends this transaction, and `room` is a frozen dataclass rather
        # than a live ORM row, but the two service reads above still belong to
        # the request's own transaction.
        await _emit_chatroom_updated(db, chatroom_id)
    return _to_out(
        room,
        has_observers=chatroom_id in with_observers,
        has_draft_readers=chatroom_id in with_draft_readers,
        is_moderator=moderator,
    )


class ChatroomMemberGroupsIn(BaseModel):
    # Bounded for the same reason the activity allowlist is: an unbounded list is
    # an unbounded write, and no room has a legitimate use for hundreds of groups.
    member_group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_BOUND_GROUPS)


class ChatroomMemberGroupsOut(BaseModel):
    member_group_ids: list[uuid.UUID]


async def _require_member_group_manage(
    db: AsyncSession,
    principal: Principal,
    project_id: uuid.UUID,
) -> None:
    """R13.31 — binding a group to a room is member management, not room editing."""
    await _require_project_cap(db, principal, project_id, Capability.PROJECT_MEMBER_MANAGE)


@chatroom_router.get("/{chatroom_id}/member-groups")
async def list_chatroom_member_groups(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ChatroomMemberGroupsOut:
    """This room's live Member Group bindings (R13.29).

    A binding whose group was since deleted is omitted. The stored row is left
    alone — the ACL already ignores it, and the repository deliberately does not
    read tenancy's `deleted_at` — but it must not be *reported*, because the
    settings UI sends this list straight back on the next edit and the PUT
    refuses a deleted id. Reading raw rows here wedged the picker permanently.
    """
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    await _require_member_group_manage(db, principal, project_id)
    bound = await ChatroomService(db).bound_group_ids(chatroom_id)
    live = await TenancyFacade(db).live_member_group_ids(sorted(bound), project_id=project_id)
    return ChatroomMemberGroupsOut(member_group_ids=sorted(live))


@chatroom_router.put("/{chatroom_id}/member-groups")
async def set_chatroom_member_groups(
    body: ChatroomMemberGroupsIn,
    chatroom_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ChatroomMemberGroupsOut:
    """Replace this room's Member Group bindings (R13.29).

    SEC: every id is checked to belong to **this room's** project before it is
    written. Without that, an owner of project A could bind a group from project B
    to a room in A and hand B's members a room they have no standing in — a
    cross-project grant assembled entirely out of ids the caller is allowed to
    know. The check reads the group rows rather than trusting the request.
    """
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    await _require_member_group_manage(db, principal, project_id)

    requested = list(dict.fromkeys(body.member_group_ids))
    if requested:
        valid = await TenancyFacade(db).live_member_group_ids(requested, project_id=project_id)
        if set(requested) - valid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="member group does not belong to this chatroom's project",
            )

    bound = await ChatroomService(db).set_bound_groups(
        chatroom_id=chatroom_id,
        group_ids=requested,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return ChatroomMemberGroupsOut(member_group_ids=sorted(bound))


@chatroom_router.delete(
    "/{chatroom_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_chatroom(
    chatroom_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    await _require_project_cap(
        db,
        principal,
        project_id,
        Capability.RESOURCE_CREATE_EDIT,
    )

    from app.api.v1._graphrag_owner_cascade import (
        purge_owner_graph_configs_external,
        soft_delete_owner_graph_configs,
    )
    from contexts.knowledge.interfaces.facade import KnowledgeFacade

    # WS6 (R11.20/AC-10): soft-delete the room's owned Concept Map config(s) in the
    # same transaction so their external stores can be purged after commit — the
    # Neo4j/Qdrant data has no FK to the room and would otherwise orphan.
    configs = await soft_delete_owner_graph_configs(
        KnowledgeFacade(db),
        owner_kind="chatroom",
        owner_id=chatroom_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    service = ChatroomService(db)
    await service.soft_delete(
        chatroom_id=chatroom_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    # DOM-4: commit the room + config soft-deletes (and their audit rows) before
    # touching any external store.
    await db.commit()
    await purge_owner_graph_configs_external(
        db,
        configs=configs,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


# --------------------------------------------------------------------------- #
# Agents subresource
# --------------------------------------------------------------------------- #


@chatroom_router.get("/{chatroom_id}/agents", response_model_exclude_none=True)
async def list_chatroom_agents(
    chatroom_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[AgentRef]:
    # Single fetch for both checks below: resolve_room_access already loads
    # the chatroom + workspace + project and resolves roles (chatroom_id in
    # its Scope is inert for role resolution — TenancyRoleResolver.roles_for
    # only reads org_id/project_id — so access.roles is exactly the project
    # membership set a separate `_project_id_for_chatroom` +
    # `roles_for(Scope(project_id=...))` call would have computed).
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    if not principal.is_admin and not access.roles:
        _raise_forbidden("not a member of the project")
    # R28.10: only the creator sees observer bindings (and roles at all) — for
    # everyone else the response is shape-identical to the pre-observer API.
    creator = is_room_creator(access, principal=principal)
    service = ChatroomService(db)
    rows = list(await service.list_agents(chatroom_id))
    if not creator:
        rows = [r for r in rows if r.role is ChatroomAgentRole.NORMAL]
    rows = rows[pagination.offset : pagination.offset + pagination.limit]
    return [
        AgentRef(
            agent_id=r.agent_id,
            role=r.role.value if creator else None,
            # [R30.37] / [R28.10]: the delegation layout is the creator's to see.
            # `None` for everyone else, which `response_model_exclude_none` drops,
            # so a non-creator's response is shape-identical to the pre-grant API.
            may_control_activities=r.may_control_activities if creator else None,
            activity_type_allowlist=list(r.activity_type_allowlist) if creator else None,
            # [R32.05]: *which* agent may read drafts is never disclosed to a
            # non-creator, even in a room whose participants are all told that some
            # agent can. The chip says "an agent here can read what you are typing";
            # this field would say which one, and that is the creator's to know.
            may_read_drafts=r.may_read_drafts if creator else None,
        )
        for r in rows
    ]


@chatroom_router.post(
    "/{chatroom_id}/agents",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def add_chatroom_agent(
    body: AgentRef,
    chatroom_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    await _require_project_cap(
        db,
        principal,
        project_id,
        Capability.RESOURCE_CREATE_EDIT,
    )
    # Agents are project-scoped; a chatroom may only bind agents from its own
    # project. The picker UI already filters to in-project agents, but guard the
    # raw endpoint too so a direct call cannot create a cross-project binding.
    agent = await AgentsFacade(db).get_agent(body.agent_id)
    if agent is None or agent.project_id != project_id:
        raise HTTPException(
            status_code=422,
            detail="agent does not belong to this chatroom's project",
        )
    role = ChatroomAgentRole(body.role) if body.role else ChatroomAgentRole.NORMAL
    if role is ChatroomAgentRole.OBSERVER:
        # R28.02: only the creator may plant an observer — a non-creator
        # moderator would be binding an agent whose output they cannot read.
        access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
        ensure_room_creator(access, principal=principal)
    service = ChatroomService(db)
    await service.add_agent(
        chatroom_id=chatroom_id,
        agent_id=body.agent_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        role=role,
        request_id=ctx.request_id,
    )
    # F-1: `observers_present` on the room DTO just changed for every viewer.
    await _emit_chatroom_updated(db, chatroom_id)


@chatroom_router.patch(
    "/{chatroom_id}/agents/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def patch_chatroom_agent_role(
    body: AgentRolePatchIn,
    chatroom_id: uuid.UUID = Path(...),
    agent_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_room_creator(access, principal=principal)
    service = ChatroomService(db)
    changed = await service.set_agent_role(
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        role=ChatroomAgentRole(body.role),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    if not changed:
        # No such binding, so there is nothing to announce.
        raise HTTPException(status_code=404, detail="agent is not bound to this chatroom")
    # F-1: promoting to (or demoting from) `observer` moves `observers_present`.
    #
    # `changed` means "the binding exists and now holds this role", NOT "a row was
    # written": `set_agent_role` returns True without writing when the agent already
    # held the target role. Re-asserting a current role therefore emits, costing each
    # viewer two cached refetches of a room that did not move. Accepted rather than
    # narrowed — distinguishing the two needs a new return value from the service,
    # and the frame is idempotent, so the cost of the no-op is a refetch while the
    # cost of getting the distinction wrong is a missed disclosure update.
    await _emit_chatroom_updated(db, chatroom_id)


@chatroom_router.patch(
    "/{chatroom_id}/agents/{agent_id}/activity-control",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def patch_chatroom_agent_activity_control(
    body: AgentActivityControlIn,
    chatroom_id: uuid.UUID = Path(...),
    agent_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    """Delegate activity start/end authority in this room to a bound agent ([R30.37]).

    ``ensure_room_creator``, matching every other authority decision about this
    room's bindings — and matching the gate on starting a round itself, which is
    the authority being handed out. Nobody who cannot start an activity may grant
    the power to.

    Every type id is resolved for the room's own project before anything is
    written. That check has to live here: the conversation context stores the
    allowlist but cannot see an activity type ([R30.05]), so the route is the only
    layer that can perform it — the same shape as ``_assert_mcp_binding_in_project``
    in ``activities.py``. Resolving before writing is what keeps a cross-project or
    deleted id a 422 rather than a stored id that quietly resolves to nothing later.
    """
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_room_creator(access, principal=principal)
    if body.granted:
        # Mirrors ck_chatroom_agents_activity_grant: authority over nothing still
        # reads as authority in every listing, so it is refused at both ends.
        if not body.activity_type_ids:
            raise HTTPException(
                status_code=422,
                detail="activity_type_ids must name at least one type when granted is true",
            )
        await _assert_activity_types_in_project(db, access.project_id, body.activity_type_ids)
    written = await ConversationFacade(db).set_agent_activity_grant(
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        granted=body.granted,
        activity_type_ids=body.activity_type_ids,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    if not written:
        raise HTTPException(status_code=404, detail="agent is not bound to this chatroom")
    await db.commit()


@chatroom_router.patch(
    "/{chatroom_id}/agents/{agent_id}/draft-access",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def patch_chatroom_agent_draft_access(
    body: AgentDraftAccessIn,
    chatroom_id: uuid.UUID = Path(...),
    agent_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    """Let one bound agent read this room's unsent text ([R32.03]).

    ``ensure_room_creator``, matching every other authority decision about this
    room's bindings. It is the strictest gate available and this is the surface that
    most deserves it: what is being handed out is the ability to read text the people
    in this room have not chosen to send.

    **Its own route rather than a field on the role patch**, for the reason
    ``activity-control`` is: a different authority with a different meaning, so the
    audit trail carries one action per decision and a role change cannot silently
    carry a grant along with it.

    Unlike ``activity-control`` there is nothing to validate before writing — no
    allowlist, no cross-context resolution — because the read-time gates in
    ``draft_tools`` are what bound this authority ([R32.04]). A grant is therefore
    exactly as narrow as the room's own activity types already are.
    """
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_room_creator(access, principal=principal)
    written = await ConversationFacade(db).set_agent_draft_grant(
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        granted=body.granted,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    if not written:
        raise HTTPException(status_code=404, detail="agent is not bound to this chatroom")
    # `drafts_readable` on the room DTO just changed for every participant: it is
    # `disclose_drafts AND has_draft_readers`, and this route moves the second term
    # ([R32.05]). Without this the pair is half-fresh — `patch_chatroom` refreshes
    # the chip when the disclosure flips, and granting the reading itself would not.
    await _emit_chatroom_updated(db, chatroom_id)


async def _assert_activity_types_in_project(
    db: AsyncSession, project_id: uuid.UUID, type_ids: list[uuid.UUID]
) -> None:
    """Refuse a grant naming a type this room's project may not use ([R30.33]).

    Duplicates are collapsed first so a list repeating one id costs one lookup.
    Every refusal is the same 422 with the same detail, whatever the reason the id
    did not resolve — missing, soft-deleted, another project's, or a platform type
    this project never opted into — because ``resolve_reachable_type`` deliberately
    collapses those four, and re-separating them here would rebuild the
    cross-tenant enumeration oracle it exists to prevent.
    """
    from contexts.activities.domain.errors import ActivityTypeNotFound
    from contexts.activities.interfaces.facade import ActivitiesFacade

    facade = ActivitiesFacade(db)
    for type_id in dict.fromkeys(type_ids):
        try:
            await facade.resolve_type_for_project(project_id=project_id, activity_type_id=type_id)
        except ActivityTypeNotFound:
            raise HTTPException(
                status_code=422,
                detail="activity type is not usable in this chatroom's project",
            ) from None


@chatroom_router.delete(
    "/{chatroom_id}/agents/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def remove_chatroom_agent(
    chatroom_id: uuid.UUID = Path(...),
    agent_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    await _require_project_cap(
        db,
        principal,
        project_id,
        Capability.RESOURCE_CREATE_EDIT,
    )
    # O-5 (R28.02/R28.09/R28.10): unbinding an observer is creator-only, like
    # binding and role change. A non-creator moderator's unbind is scoped to
    # normal bindings, so an observer target is a silent no-op that returns the
    # same 204 as any other unbind — never a 403 that would out a hidden
    # observer, and the role-scoped delete closes the read-then-delete race.
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    service = ChatroomService(db)
    await service.remove_agent(
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
        restrict_to_normal=not is_room_creator(access, principal=principal),
    )
    # F-1. Emitted unconditionally, including for the role-scoped no-op above:
    # `remove_agent` does not report whether it matched, and branching on that
    # would have to be added here — where a non-creator's unbind attempt on an
    # observer must stay indistinguishable from a successful one (O-5). A frame
    # costs each viewer one cached GET; an oracle costs the observer's cover.
    await _emit_chatroom_updated(db, chatroom_id)


# --------------------------------------------------------------------------- #
# Members — human author roster for resolving chat-message display names
# --------------------------------------------------------------------------- #


@chatroom_router.get("/{chatroom_id}/members")
async def list_chatroom_members(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[ChatroomMemberOut]:
    """Resolve human participants to display names so the client can label
    message authors (REST history + live WS messages share one map).

    Only ``user_id`` + ``display_name`` is returned — never email — so a room
    member (including a guest) cannot harvest other participants' login
    identifiers. The id set is the union of distinct human message authors and
    enrolled guests; a guest's per-room display name takes precedence over their
    account display name. Names left unset resolve to ``null`` and the client
    falls back to a short id.
    """
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    conv = ConversationFacade(db)
    if not principal.is_admin:
        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(
            principal,
            Scope(project_id=project_id, chatroom_id=chatroom_id),
        )
        is_guest = await conv.is_chatroom_guest(
            chatroom_id=chatroom_id,
            user_id=principal.user_id,
        )
        if not roles and not is_guest:
            _raise_forbidden("not a participant of this room")
    guest_names = {g.user_id: g.display_name for g in await conv.list_guests(chatroom_id)}
    sender_ids = await conv.distinct_user_sender_ids(chatroom_id)
    all_ids = sender_ids | set(guest_names)
    account_names = await IdentityFacade(db).get_display_names(list(all_ids))

    return [
        ChatroomMemberOut(
            user_id=uid,
            display_name=prefer_guest_label(guest_names.get(uid), account_names.get(uid)),
        )
        for uid in all_ids
    ]


# --------------------------------------------------------------------------- #
# Guest link — R13.05–R13.07
# --------------------------------------------------------------------------- #


@chatroom_router.get("/{chatroom_id}/guest-link")
async def read_guest_link(
    request: Request,
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> GuestLinkOut:
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    await _require_project_cap(
        db,
        principal,
        project_id,
        Capability.GUEST_LINK_MANAGE,
    )
    service = ChatroomService(db)
    room = await service.get(chatroom_id)
    base = f"{request.url.scheme}://{request.url.netloc}"
    return GuestLinkOut(
        url=f"{base}/g/{room.id}/{room.guest_token}",
        chatroom_id=room.id,
        guest_token=room.guest_token,
    )


# --------------------------------------------------------------------------- #
# /compact slash command — G.10
# --------------------------------------------------------------------------- #


@chatroom_router.post(
    "/{chatroom_id}/compact",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Force context compaction for active agents in this room (G.10)",
)
async def compact_chatroom(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    """Trigger an immediate compaction pass for the room.

    Records a one-shot intent flag (K.2): the next agent turn in this room
    reads + clears it and forces a compaction pass before its provider call
    (``turn_engine._consume_compact_flag``). Returns 202 so the frontend slash
    command completes immediately.
    """
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    await _require_project_cap(db, principal, project_id, Capability.CHAT_SEND)
    service = ChatroomService(db)
    await service.request_compaction(chatroom_id)
    return {"status": "accepted", "chatroom_id": str(chatroom_id)}


# --------------------------------------------------------------------------- #
# Presence snapshot — FIX-05
# --------------------------------------------------------------------------- #


class PresenceOut(BaseModel):
    user_ids: list[str]


@chatroom_router.get(
    "/{chatroom_id}/presence",
    summary="Snapshot of users currently present via WebSocket",
)
async def get_chatroom_presence(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> PresenceOut:
    from contexts.conversation.application.access import (
        ensure_can_read,
        resolve_room_access,
    )
    from contexts.conversation.interfaces import PresenceTracker

    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    members = await PresenceTracker().list_room(chatroom_id)
    return PresenceOut(user_ids=[str(uid) for uid in members])


# --------------------------------------------------------------------------- #
# Approvals read side — F-13
# --------------------------------------------------------------------------- #


@chatroom_router.get(
    "/{chatroom_id}/approvals",
    summary="List approval gates raised in a chatroom",
)
async def list_chatroom_approvals(
    chatroom_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[ApprovalWithVotesOut]:
    """Room-scoped read side for F-13: the connect-time client reconcile fetches
    this list to discover an approval gate whose `approval.requested` WS frame
    was missed while disconnected. Gated the same way as every other room read
    (`resolve_room_access` + `ensure_can_read`), not by project membership --
    the room is the resource here, and a platform admin passes `ensure_can_read`
    the same way every other room read already does. Rows created before the
    `chatroom_id` column existed are simply absent, not an error (Q-4 of the
    task dossier: no backfill)."""
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    orchestration = OrchestrationFacade(db)
    paired = await orchestration.list_approvals_for_chatroom_with_votes(chatroom_id)
    paired = paired[pagination.offset : pagination.offset + pagination.limit]
    return [approval_with_votes_out(a, votes) for a, votes in paired]


__all__ = ["chatroom_router", "workspace_router"]
