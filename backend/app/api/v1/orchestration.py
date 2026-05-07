"""`/api/orchestration/*` — read-only orchestration surface (G.10).

Exposes approval gates, instruct chains, sub-agent instances, and A2A DLQ
entries for the frontend. All mutations flow through the workflow engine
(Phase H). DLQ access is restricted to admin principals.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.orchestration.application.approval_service import ApprovalService
from contexts.orchestration.application.instruct_service import InstructService
from contexts.orchestration.application.subagent_service import SubagentService
from contexts.orchestration.infrastructure.a2a_streams import read_dlq
from shared_kernel.auth.dependencies import current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/orchestration", tags=["orchestration"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _approval_out(approval: Any, votes: list[Any] | None = None) -> dict[str, Any]:
    out = {
        "id": str(approval.id),
        "workflow_run_id": str(approval.workflow_run_id),
        "mode": approval.mode.value,
        "leader_agent_id": str(approval.leader_agent_id),
        "approver_agent_ids": [str(a) for a in approval.approver_agent_ids],
        "timeout_seconds": approval.timeout_seconds,
        "state": approval.state.value,
        "started_at": approval.started_at.isoformat(),
        "ended_at": approval.ended_at.isoformat() if approval.ended_at else None,
    }
    if votes is not None:
        out["votes"] = [
            {
                "approval_id": str(v.approval_id),
                "voter_agent_id": str(v.voter_agent_id),
                "vote": v.vote,
                "rationale": v.rationale,
                "cast_at": v.cast_at.isoformat(),
            }
            for v in votes
        ]
    return out


def _instruction_out(instruction: Any) -> dict[str, Any]:
    return {
        "id": str(instruction.id),
        "chain_id": str(instruction.chain_id),
        "path": [str(p) for p in instruction.path],
        "depth": instruction.depth,
        "issuer_agent_id": str(instruction.issuer_agent_id),
        "target_agent_id": str(instruction.target_agent_id),
        "payload": instruction.payload,
        "state": instruction.state.value,
        "issued_at": instruction.issued_at.isoformat(),
        "resolved_at": instruction.resolved_at.isoformat() if instruction.resolved_at else None,
    }


def _instance_out(instance: Any) -> dict[str, Any]:
    return {
        "id": str(instance.id),
        "agent_id": str(instance.agent_id),
        "parent_id": str(instance.parent_id) if instance.parent_id else None,
        "chatroom_id": str(instance.chatroom_id) if instance.chatroom_id else None,
        "run_context": instance.run_context,
        "task_description": instance.task_description,
        "state": instance.state,
        "spawned_at": instance.spawned_at.isoformat(),
        "destroyed_at": instance.destroyed_at.isoformat() if instance.destroyed_at else None,
    }


# ---------------------------------------------------------------------------
# Approval endpoints (G.6)
# ---------------------------------------------------------------------------


@router.get(
    "/approvals/{approval_id}",
    summary="Get approval gate with votes",
)
async def get_approval(
    approval_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    svc = ApprovalService(db)
    approval = await svc.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="approval not found")
    votes = await svc.get_votes(approval_id)
    return _approval_out(approval, votes)


@router.get(
    "/workflow-runs/{workflow_run_id}/approvals",
    summary="List approvals for a workflow run",
)
async def list_approvals_for_run(
    workflow_run_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[dict[str, Any]]:
    svc = ApprovalService(db)
    approvals = await svc.list_for_run(workflow_run_id)
    return [_approval_out(a) for a in approvals]


# ---------------------------------------------------------------------------
# Instruct chain endpoints (G.7 — admin backstage)
# ---------------------------------------------------------------------------


@router.get(
    "/instructions/{instruction_id}",
    summary="Get a single instruction record",
)
async def get_instruction(
    instruction_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    svc = InstructService(db)
    instruction = await svc.get_instruction(instruction_id)
    if instruction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instruction not found")
    return _instruction_out(instruction)


@router.get(
    "/chains/{chain_id}/instructions",
    summary="List all instructions in a chain",
)
async def list_instructions_for_chain(
    chain_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[dict[str, Any]]:
    svc = InstructService(db)
    instructions = await svc.list_for_chain(chain_id)
    return [_instruction_out(i) for i in instructions]


# ---------------------------------------------------------------------------
# Sub-agent endpoints (G.8)
# ---------------------------------------------------------------------------


@router.get(
    "/instances/{parent_instance_id}/children",
    summary="List live sub-agents for a parent instance",
)
async def list_subagent_children(
    parent_instance_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[dict[str, Any]]:
    svc = SubagentService(db)
    children = await svc.list_children(parent_instance_id)
    return [_instance_out(c) for c in children]


# ---------------------------------------------------------------------------
# DLQ viewer (G.10 — admin only)
# ---------------------------------------------------------------------------


@router.get(
    "/agents/{agent_id}/dlq",
    summary="Read A2A DLQ entries for an agent (admin only)",
)
async def get_agent_dlq(
    agent_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
) -> list[dict[str, Any]]:
    if not principal.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin access required",
        )
    return await read_dlq(agent_id)


__all__ = ["router"]
