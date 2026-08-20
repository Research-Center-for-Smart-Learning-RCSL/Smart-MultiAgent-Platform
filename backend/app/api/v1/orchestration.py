"""`/api/orchestration/*` — read-only orchestration surface (G.10).

Exposes approval gates, instruct chains, sub-agent instances, and A2A DLQ
entries for the frontend. All mutations flow through the workflow engine
(Phase H).

AuthZ (API-2 + R15.24 — a dual track, decided per record, not per route):
- A record that names a chat room — an approval gate raised in one (G.6), an
  agent instance running in one (G.8) — is readable by exactly the principals
  who may read that room, evaluated by the room ACL itself
  (`can_read_orchestration_record`). The gate therefore tracks every room tier,
  including ones added later, without this module knowing what they are.
- A record that names none — an instruction (G.7), and any approval or instance
  whose room was deleted (both FKs are `ON DELETE SET NULL`) — is backstage and
  follows [R14.10]: Admin and Project/Org Owners.
- Listings omit rows the caller may not read rather than refusing the request,
  and filter *before* paginating so the page length discloses nothing (R15.24).
- A2A DLQ (G.10) is the one exception, still bare project membership: its viewer
  renders inside the chatroom settings view and that surface's audience has not
  been established (dossier 2026-08-20-orchestration-room-scoped-reads, FU-1).

SEC: denial on the room branch answers 404 with the same body a missing record
answers. A record in a room the caller cannot open must be indistinguishable
from one that does not exist, or the 403 itself reports another group's session.

Every handler resolves its path UUID to a project before returning data —
without this an authenticated caller could read any tenant's orchestration
state by enumerating IDs.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.conversation.interfaces.access import (
    can_read_orchestration_record,
    filter_readable_by_room,
)
from contexts.orchestration.application.approval_service import ApprovalService
from contexts.orchestration.application.instruct_service import InstructService
from contexts.orchestration.application.subagent_service import SubagentService
from contexts.orchestration.domain.models import (
    ApprovalMode,
    ApprovalState,
    InstructionState,
)
from contexts.orchestration.infrastructure.a2a_streams import read_dlq
from contexts.orchestration.interfaces.facade import OrchestrationFacade
from shared_kernel.auth.dependencies import current_principal, get_role_resolver
from shared_kernel.auth.permissions import Principal, RoleResolver, Scope
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/orchestration", tags=["orchestration"])


# ---------------------------------------------------------------------------
# AuthZ helpers
# ---------------------------------------------------------------------------


async def _assert_project_member(
    principal: Principal,
    project_id: uuid.UUID,
    resolver: RoleResolver,
) -> None:
    """Require the caller to hold any role in `project_id` (admin always passes).

    The DLQ route's gate, and only that one (FU-1). Every other read here is
    room-scoped or backstage — see `_assert_can_read_record`. Do not reach for
    this when adding a route: bare project membership is what R15.24 exists to
    stop being the answer.
    """
    if principal.is_admin:
        return
    roles = await resolver.roles_for(principal, Scope(project_id=project_id))
    if not roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="caller is not a member of the project",
        )


async def _assert_can_read_record(
    db: AsyncSession,
    principal: Principal,
    resolver: RoleResolver,
    *,
    chatroom_id: uuid.UUID | None,
    project_id: uuid.UUID,
    what: str,
) -> None:
    """R15.24's dual track for one record, mapped onto status codes.

    SEC: the room branch denies with 404 and the exact body `_not_found` gives
    for a record that is not there, so the response cannot be used to confirm
    that a gate was raised in a room the caller cannot open. The backstage
    branch has no room to hide and says plainly what it wants.
    """
    if await can_read_orchestration_record(
        db,
        principal=principal,
        chatroom_id=chatroom_id,
        project_id=project_id,
        resolver=resolver,
    ):
        return
    if chatroom_id is not None:
        raise _not_found(what)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="backstage records require Admin or a project owner",
    )


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


# ---------------------------------------------------------------------------
# Response models — typed contract for the read surface (G.6–G.10)
# ---------------------------------------------------------------------------


class ApprovalVoteOut(BaseModel):
    approval_id: str
    voter_agent_id: str
    vote: bool
    rationale: str | None
    cast_at: str


class ApprovalOut(BaseModel):
    id: str
    workflow_run_id: str
    mode: ApprovalMode
    leader_agent_id: str
    approver_agent_ids: list[str]
    timeout_seconds: int
    state: ApprovalState
    started_at: str
    ended_at: str | None


class ApprovalWithVotesOut(ApprovalOut):
    votes: list[ApprovalVoteOut]


class InstructionOut(BaseModel):
    id: str
    chain_id: str
    path: list[str]
    depth: int
    issuer_agent_id: str
    target_agent_id: str
    payload: dict[str, Any]
    state: InstructionState
    issued_at: str
    resolved_at: str | None


class AgentInstanceOut(BaseModel):
    id: str
    agent_id: str
    parent_id: str | None
    chatroom_id: str | None
    run_context: dict[str, Any]
    task_description: str | None
    state: str
    spawned_at: str
    destroyed_at: str | None


class DlqEntryOut(BaseModel):
    stream_entry_id: str
    stream_id: str
    envelope: str
    # `read_dlq` stringifies every Redis field; `int` coerces "3" -> 3 so the wire
    # value matches the frontend's numeric type (dossier Q-1).
    attempt_count: int
    last_error: str
    moved_at: str


# ---------------------------------------------------------------------------
# Helpers — domain row → response model
# ---------------------------------------------------------------------------


def vote_out(v: Any) -> ApprovalVoteOut:
    return ApprovalVoteOut(
        approval_id=str(v.approval_id),
        voter_agent_id=str(v.voter_agent_id),
        vote=v.vote,
        rationale=v.rationale,
        cast_at=v.cast_at.isoformat(),
    )


def approval_out(approval: Any) -> ApprovalOut:
    return ApprovalOut(
        id=str(approval.id),
        workflow_run_id=str(approval.workflow_run_id),
        mode=approval.mode,
        leader_agent_id=str(approval.leader_agent_id),
        approver_agent_ids=[str(a) for a in approval.approver_agent_ids],
        timeout_seconds=approval.timeout_seconds,
        state=approval.state,
        started_at=approval.started_at.isoformat(),
        ended_at=approval.ended_at.isoformat() if approval.ended_at else None,
    )


def approval_with_votes_out(approval: Any, votes: list[Any]) -> ApprovalWithVotesOut:
    """Public (not underscore-prefixed): reused by chatrooms.py's room-scoped
    approvals list (F-13) so the two read surfaces cannot silently diverge."""
    return ApprovalWithVotesOut(
        **approval_out(approval).model_dump(),
        votes=[vote_out(v) for v in votes],
    )


def _instruction_out(instruction: Any) -> InstructionOut:
    return InstructionOut(
        id=str(instruction.id),
        chain_id=str(instruction.chain_id),
        path=[str(p) for p in instruction.path],
        depth=instruction.depth,
        issuer_agent_id=str(instruction.issuer_agent_id),
        target_agent_id=str(instruction.target_agent_id),
        payload=instruction.payload,
        state=instruction.state,
        issued_at=instruction.issued_at.isoformat(),
        resolved_at=instruction.resolved_at.isoformat() if instruction.resolved_at else None,
    )


def _instance_out(instance: Any) -> AgentInstanceOut:
    return AgentInstanceOut(
        id=str(instance.id),
        agent_id=str(instance.agent_id),
        parent_id=str(instance.parent_id) if instance.parent_id else None,
        chatroom_id=str(instance.chatroom_id) if instance.chatroom_id else None,
        run_context=instance.run_context,
        task_description=instance.task_description,
        state=instance.state,
        spawned_at=instance.spawned_at.isoformat(),
        destroyed_at=instance.destroyed_at.isoformat() if instance.destroyed_at else None,
    )


# ---------------------------------------------------------------------------
# Approval endpoints (G.6 — room ACL when the gate names a room, else backstage)
# ---------------------------------------------------------------------------


@router.get(
    "/approvals/{approval_id}",
    summary="Get approval gate with votes",
)
async def get_approval(
    approval_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
) -> ApprovalWithVotesOut:
    svc = ApprovalService(db)
    project_id = await svc.resolve_project(approval_id)
    if project_id is None:
        raise _not_found("approval")
    approval = await svc.get_approval(approval_id)
    if approval is None:  # pragma: no cover — resolved above
        raise _not_found("approval")
    # Read before gate: the record's own `chatroom_id` chooses which track it is
    # judged on. Nothing is returned before the gate runs.
    await _assert_can_read_record(
        db,
        principal,
        resolver,
        chatroom_id=approval.chatroom_id,
        project_id=project_id,
        what="approval",
    )
    votes = await svc.get_votes(approval_id)
    return approval_with_votes_out(approval, votes)


@router.get(
    "/workflow-runs/{workflow_run_id}/approvals",
    summary="List approvals for a workflow run",
)
async def list_approvals_for_run(
    workflow_run_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(db_session),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
) -> list[ApprovalOut]:
    svc = ApprovalService(db)
    project_id = await svc.resolve_run_project(workflow_run_id)
    if project_id is None:
        raise _not_found("workflow run")
    approvals = await svc.list_for_run(workflow_run_id)
    # SEC: filter, then slice. Slicing first would let the page length report how
    # many rows were withheld (R15.24). A caller who may read none gets [].
    approvals = await filter_readable_by_room(
        db,
        principal=principal,
        rows=approvals,
        chatroom_id_of=lambda a: a.chatroom_id,
        project_id=project_id,
        resolver=resolver,
    )
    approvals = approvals[pagination.offset : pagination.offset + pagination.limit]
    return [approval_out(a) for a in approvals]


# ---------------------------------------------------------------------------
# Instruct chain endpoints (G.7 — backstage: Admin + project owners, R14.10)
# ---------------------------------------------------------------------------


@router.get(
    "/instructions/{instruction_id}",
    summary="Get a single instruction record",
)
async def get_instruction(
    instruction_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
) -> InstructionOut:
    svc = InstructService(db)
    project_id = await svc.resolve_instruction_project(instruction_id)
    if project_id is None:
        raise _not_found("instruction")
    # `instructions` carries no chatroom column, so every instruction is
    # backstage ([R14.10]). Its payload may still quote room content — see FU-2.
    await _assert_can_read_record(
        db,
        principal,
        resolver,
        chatroom_id=None,
        project_id=project_id,
        what="instruction",
    )
    instruction = await svc.get_instruction(instruction_id)
    if instruction is None:  # pragma: no cover — resolved above
        raise _not_found("instruction")
    return _instruction_out(instruction)


@router.get(
    "/chains/{chain_id}/instructions",
    summary="List all instructions in a chain",
)
async def list_instructions_for_chain(
    chain_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(db_session),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
) -> list[InstructionOut]:
    svc = InstructService(db)
    project_id = await svc.resolve_chain_project(chain_id)
    if project_id is None:
        raise _not_found("chain")
    await _assert_can_read_record(
        db,
        principal,
        resolver,
        chatroom_id=None,
        project_id=project_id,
        what="chain",
    )
    instructions = await svc.list_for_chain(chain_id)
    instructions = instructions[pagination.offset : pagination.offset + pagination.limit]
    return [_instruction_out(i) for i in instructions]


# ---------------------------------------------------------------------------
# Sub-agent endpoints (G.8 — room ACL per instance, else backstage)
# ---------------------------------------------------------------------------


@router.get(
    "/workflow-runs/{workflow_run_id}/subagents",
    summary="List sub-agents spawned during a workflow run",
)
async def list_run_subagents(
    workflow_run_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(db_session),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
) -> list[AgentInstanceOut]:
    facade = OrchestrationFacade(db)
    project_id = await facade.resolve_workflow_run_project(workflow_run_id)
    if project_id is None:
        raise _not_found("workflow run")
    instances = await facade.list_workflow_run_subagents(workflow_run_id)
    instances = await filter_readable_by_room(
        db,
        principal=principal,
        rows=instances,
        chatroom_id_of=lambda i: i.chatroom_id,
        project_id=project_id,
        resolver=resolver,
    )
    instances = instances[pagination.offset : pagination.offset + pagination.limit]
    return [_instance_out(i) for i in instances]


@router.get(
    "/instances/{parent_instance_id}/children",
    summary="List live sub-agents for a parent instance",
)
async def list_subagent_children(
    parent_instance_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(db_session),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
) -> list[AgentInstanceOut]:
    svc = SubagentService(db)
    project_id = await svc.resolve_project(parent_instance_id)
    if project_id is None:
        raise _not_found("agent instance")
    parent = await svc.get_instance(parent_instance_id)
    if parent is None:  # pragma: no cover — resolved above
        raise _not_found("agent instance")
    # The parent gates the enumeration; the children are filtered too, because a
    # child instance carries its own `chatroom_id` and need not name the
    # parent's room.
    await _assert_can_read_record(
        db,
        principal,
        resolver,
        chatroom_id=parent.chatroom_id,
        project_id=project_id,
        what="agent instance",
    )
    children = await svc.list_children(parent_instance_id)
    children = await filter_readable_by_room(
        db,
        principal=principal,
        rows=children,
        chatroom_id_of=lambda c: c.chatroom_id,
        project_id=project_id,
        resolver=resolver,
    )
    children = children[pagination.offset : pagination.offset + pagination.limit]
    return [_instance_out(c) for c in children]


# ---------------------------------------------------------------------------
# DLQ viewer (G.10 — project members; deliberately not narrowed, FU-1)
# ---------------------------------------------------------------------------


@router.get(
    "/agents/{agent_id}/dlq",
    summary="Read A2A DLQ entries for an agent",
)
async def get_agent_dlq(
    agent_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
) -> list[DlqEntryOut]:
    agent = await AgentsFacade(db).get_agent(agent_id, include_deleted=True)
    if agent is None:
        raise _not_found("agent")
    await _assert_project_member(principal, agent.project_id, resolver)
    return [DlqEntryOut(**d) for d in await read_dlq(agent_id)]


__all__ = ["approval_out", "approval_with_votes_out", "router", "vote_out"]
