"""Arq tasks for workflow approval and instruct gate resume (K.4).

- workflow_resume_approval:  Resume a run parked on approval_gate once it resolves.
- workflow_resume_instruct:  Resume a run parked on instruct once it settles.
- workflow_instruct_timeout: Mark an instruct deadline timeout, then resume.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from loguru import logger

from app.workers.tasks.workflow_common import (
    _CLAIM_RESTORE_TTL_S,
    _RESUME_RETRY_DELAY_S,
    _RESUME_RETRY_MAX_ATTEMPTS,
    _emit_resumed,
    _restore_claim,
    _run_is_terminal,
)

# Poll budget bridging the gap between an approval resolving inside an agent
# turn's tool call and that turn's single end-of-turn commit (the turn engine
# commits once). 3 s × 210 ≈ 10.5 min ≥ the 600 s job timeout, so any voting
# turn has committed (or rolled back) before the budget is spent.
_APPROVAL_RESUME_DELAY_S = 3
_APPROVAL_RESUME_MAX_ATTEMPTS = 210


async def workflow_resume_approval(ctx: dict[str, Any], approval_id: str, attempt: int = 0) -> str:
    """Resume a workflow run parked on ``approval_gate`` once the gate resolves (K.4).

    Enqueued by ``ApprovalService`` on vote-driven and timeout-driven resolution.
    A vote resolves the gate inside the voting agent's turn, which only commits
    at turn end, so this job re-checks (bounded) until the resolved state is
    visible, then atomically claims ``wf:approval:{id}`` and resumes at the
    approved/rejected/timeout port. Non-workflow (room-only) approvals carry no
    claim key and no-op here.
    """
    import json

    from contexts.orchestration.domain.models import ApprovalState
    from contexts.orchestration.interfaces.facade import OrchestrationFacade
    from shared_kernel.auth.clients import get_redis
    from shared_kernel.db.session import async_session

    redis = get_redis()
    key = f"wf:approval:{approval_id}"
    if await redis.get(key) is None:
        return "noop:no_claim"

    aid = uuid.UUID(approval_id)
    async with async_session() as db:
        facade = OrchestrationFacade(db)
        approval = await facade.get_approval(aid)
        if approval is None:
            await redis.delete(key)
            return "noop:gone"
        if approval.state == ApprovalState.PENDING:
            # Resolver's transaction not yet committed (long turn) or it rolled
            # back. Retry within budget; the gate-timeout path will resolve and
            # re-enqueue if votes never commit.
            if attempt < _APPROVAL_RESUME_MAX_ATTEMPTS:
                await ctx["redis"].enqueue_job(
                    "workflow_resume_approval",
                    approval_id,
                    attempt + 1,
                    _defer_by=timedelta(seconds=_APPROVAL_RESUME_DELAY_S),
                )
            return "pending:retry"

        votes = await facade.get_approval_votes(aid)
        port = _approval_port(approval, votes)

        ttl = await redis.ttl(key)
        claimed = await redis.getdel(key)
        if claimed is None:
            return "noop:claimed_elsewhere"
        info = json.loads(claimed)

        from contexts.workflow.application.run_engine import RunEngine

        engine = RunEngine(db)
        resumed = await engine.resume_at_port(uuid.UUID(info["run_id"]), info["node_id"], port)
        if not resumed:
            await db.commit()  # persist side effects (e.g. workflow-deleted FAILED)
            if await _run_is_terminal(db, info["run_id"]):
                return "noop:terminal"
            # Claim-before-verify: run not WAITING yet (parking commit pending
            # or a parallel sibling running) — restore the claim and retry.
            # Shares the attempt budget with the pending-poll above.
            await _restore_claim(redis, key, claimed, ttl)
            if attempt < _APPROVAL_RESUME_MAX_ATTEMPTS:
                await ctx["redis"].enqueue_job(
                    "workflow_resume_approval",
                    approval_id,
                    attempt + 1,
                    _defer_by=timedelta(seconds=_APPROVAL_RESUME_DELAY_S),
                )
                return "not_waiting:retry"
            return "not_waiting:gave_up"
        await _emit_resumed(db, info["run_id"], info["node_id"], reason=f"approval:{port}")
        await db.commit()
        await engine.dispatch_enqueues(ctx["redis"])

    logger.bind(event="workflow_approval_resumed", approval_id=approval_id, port=port).info(
        "workflow resumed after approval"
    )
    return f"resumed:{port}"


def _approval_port(approval: Any, votes: list[Any]) -> str:
    """Map a resolved approval to the approval_gate output port."""
    from contexts.orchestration.domain.models import ApprovalState

    if approval.state == ApprovalState.APPROVED:
        return "approved"
    if approval.state == ApprovalState.REJECTED:
        return "rejected"
    # TIMEOUT_LEADER — the leader's last vote breaks it; no leader vote → timeout.
    leader_votes = [v for v in votes if v.voter_agent_id == approval.leader_agent_id]
    if leader_votes:
        return "approved" if leader_votes[-1].vote else "rejected"
    return "timeout"


async def workflow_resume_instruct(ctx: dict[str, Any], instruction_id: str, attempt: int = 0) -> str:
    """Resume a workflow run parked on ``instruct`` once the instruction settles (K.4).

    Enqueued post-commit by the A2A handler (completion) and by
    ``workflow_instruct_timeout`` (deadline). The committed instruction state
    decides the port, so completion and timeout can't disagree; ``GETDEL`` on
    ``wf:instruct:{id}`` makes the resume single-shot. Non-workflow instructs
    carry no claim key and no-op.

    Also populates the instruct node's ``output_variable`` (the executor only
    does so on the non-parked path) and, claim-before-verify, restores the
    claim + retries bounded when the run is not WAITING yet.
    """
    import json

    from contexts.orchestration.domain.models import InstructionState
    from contexts.orchestration.interfaces.facade import OrchestrationFacade
    from shared_kernel.auth.clients import get_redis
    from shared_kernel.db.session import async_session

    redis = get_redis()
    key = f"wf:instruct:{instruction_id}"
    if await redis.get(key) is None:
        return "noop:no_claim"

    iid = uuid.UUID(instruction_id)
    async with async_session() as db:
        instruction = await OrchestrationFacade(db).get_instruction(iid)
        if instruction is None:
            await redis.delete(key)
            return "noop:gone"
        if instruction.state == InstructionState.COMPLETED:
            port = "success"
        elif instruction.state in (InstructionState.TIMEOUT, InstructionState.REJECTED_LOOP):
            port = "failure"
        else:
            return "pending"  # issued/delivered — not settled yet

        ttl = await redis.ttl(key)
        claimed = await redis.getdel(key)
        if claimed is None:
            return "noop:claimed_elsewhere"
        info = json.loads(claimed)

        from contexts.workflow.application.run_engine import RunEngine

        if port == "success":
            # Populate the node's output_variable BEFORE resuming so downstream
            # nodes (and resume_at_port's variable snapshot) see it.
            await _store_instruct_output(db, info["run_id"], info["node_id"], str(iid))

        engine = RunEngine(db)
        resumed = await engine.resume_at_port(uuid.UUID(info["run_id"]), info["node_id"], port)
        if not resumed:
            await db.commit()  # persist side effects (output_variable / failed run)
            if await _run_is_terminal(db, info["run_id"]):
                return "noop:terminal"
            # Claim-before-verify: restore the claim and retry bounded.
            await _restore_claim(redis, key, claimed, ttl)
            if attempt < _RESUME_RETRY_MAX_ATTEMPTS:
                await ctx["redis"].enqueue_job(
                    "workflow_resume_instruct",
                    instruction_id,
                    attempt + 1,
                    _defer_by=timedelta(seconds=_RESUME_RETRY_DELAY_S),
                )
                return "not_waiting:retry"
            return "not_waiting:gave_up"
        await _emit_resumed(db, info["run_id"], info["node_id"], reason=f"instruct:{port}")
        await db.commit()
        await engine.dispatch_enqueues(ctx["redis"])

    logger.bind(event="workflow_instruct_resumed", instruction_id=instruction_id, port=port).info(
        "workflow resumed after instruct"
    )
    return f"resumed:{port}"


async def workflow_instruct_timeout(ctx: dict[str, Any], instruction_id: str) -> str:
    """Deadline for a parked ``instruct`` node — mark timeout, then resume (K.4)."""
    from contexts.orchestration.domain.models import InstructionState
    from contexts.orchestration.interfaces.facade import OrchestrationFacade
    from shared_kernel.db.session import async_session

    iid = uuid.UUID(instruction_id)
    async with async_session() as db:
        facade = OrchestrationFacade(db)
        instruction = await facade.get_instruction(iid)
        if instruction is None or instruction.state in (
            InstructionState.COMPLETED,
            InstructionState.TIMEOUT,
            InstructionState.REJECTED_LOOP,
        ):
            return "noop"
        await facade.mark_instruct_timeout(iid)
        await db.commit()

    await ctx["redis"].enqueue_job("workflow_resume_instruct", instruction_id)
    logger.bind(instruction_id=instruction_id).info("instruct deadline: marked timeout")
    return "timed_out"


async def _store_instruct_output(db: Any, run_id: str, node_id: str, instruction_id: str) -> None:
    """Populate the instruct node's ``output_variable`` on the parked path.

    The executor writes it only on the non-parked (``wait_for_completion=False``)
    branch; a parked node resumed here never surfaced anything. The instruction's
    reply *text* is not persisted anywhere (the A2A turn result lives only in
    memory in ``a2a_handler``), so — matching the non-parked path's semantics —
    the instruction id is stored. Best-effort: a population failure must not
    block the resume. Idempotent across resume retries.
    """
    try:
        from contexts.workflow.infrastructure.repositories import (
            WorkflowRepository,
            WorkflowRunRepository,
        )

        runs = WorkflowRunRepository(db)
        run = await runs.get(uuid.UUID(run_id))
        if run is None:
            return
        workflow = await WorkflowRepository(db).get(run.workflow_id, include_deleted=True)
        if workflow is None:
            return
        node = next(
            (n for n in workflow.definition.get("nodes", []) if n.get("id") == node_id),
            None,
        )
        output_variable = (node or {}).get("config", {}).get("output_variable")
        if not output_variable:
            return
        variables = dict(run.variables)
        variables[output_variable] = instruction_id
        await runs.update_variables(uuid.UUID(run_id), variables)
    except Exception:
        logger.bind(run_id=run_id, node_id=node_id).exception(
            "instruct resume: output_variable population failed"
        )
