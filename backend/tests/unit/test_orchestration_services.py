"""Unit tests for orchestration services: ApprovalService (evaluate_votes +
full gate lifecycle), InstructService (loop detection, depth/count/wall-clock
caps), SubagentService (depth-1 constraint, concurrency cap, inheritance).

Covers: G.6 approval SINGLE/MAJORITY/CONSENSUS + ties + timeout-leader,
G.7 instruct issue with all 4 R15.16 rules, G.8 subagent spawn/destroy
with depth guard + concurrency cap + inherited context + workflow callback.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.orchestration.application.approval_service import ApprovalService
from contexts.orchestration.application.instruct_service import InstructService
from contexts.orchestration.application.subagent_service import SubagentService
from contexts.orchestration.domain.errors import (
    InstructBudgetExceeded,
    InstructLoopDetected,
    SubagentConcurrencyExceeded,
    SubagentDepthExceeded,
)
from contexts.orchestration.domain.models import (
    Approval,
    ApprovalGateConfig,
    ApprovalMode,
    ApprovalState,
    ApprovalVote,
    AgentInstance,
    Instruction,
    InstructionState,
)

_NOW = datetime(2026, 6, 23, 12, 0, 0, tzinfo=UTC)
_AGENT_A = uuid.uuid4()
_AGENT_B = uuid.uuid4()
_AGENT_C = uuid.uuid4()
_RUN = uuid.uuid4()
_ROOM = uuid.uuid4()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vote(agent: uuid.UUID, vote: bool) -> ApprovalVote:
    return ApprovalVote(
        approval_id=uuid.uuid4(),
        voter_agent_id=agent,
        vote=vote,
        rationale=None,
        cast_at=_NOW,
    )


def _approval(
    *,
    mode: ApprovalMode = ApprovalMode.SINGLE,
    leader: uuid.UUID = _AGENT_A,
    approvers: tuple[uuid.UUID, ...] | None = None,
    state: ApprovalState = ApprovalState.PENDING,
) -> Approval:
    return Approval(
        id=uuid.uuid4(),
        workflow_run_id=_RUN,
        mode=mode,
        leader_agent_id=leader,
        approver_agent_ids=approvers or (_AGENT_A, _AGENT_B, _AGENT_C),
        timeout_seconds=300,
        state=state,
        started_at=_NOW,
        ended_at=None,
    )


def _instruction(
    *,
    state: InstructionState = InstructionState.ISSUED,
    chain_id: uuid.UUID | None = None,
    depth: int = 0,
) -> Instruction:
    return Instruction(
        id=uuid.uuid4(),
        chain_id=chain_id or uuid.uuid4(),
        path=(_AGENT_A,),
        depth=depth,
        issuer_agent_id=_AGENT_A,
        target_agent_id=_AGENT_B,
        payload={"task": "do"},
        state=state,
        issued_at=_NOW,
        resolved_at=None,
    )


def _instance(
    *,
    instance_id: uuid.UUID | None = None,
    parent_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    state: str = "running",
) -> AgentInstance:
    return AgentInstance(
        id=instance_id or uuid.uuid4(),
        agent_id=agent_id or _AGENT_A,
        parent_id=parent_id,
        chatroom_id=_ROOM,
        run_context={},
        task_description="test task",
        state=state,
        spawned_at=_NOW,
        destroyed_at=None,
    )


def _make_approval_service(
    *,
    approvals: AsyncMock | None = None,
    votes: AsyncMock | None = None,
) -> ApprovalService:
    db = AsyncMock()
    svc = ApprovalService(db)
    if approvals is not None:
        svc._approvals = approvals
    if votes is not None:
        svc._votes = votes
    return svc


def _make_instruct_service(
    *,
    instructions: AsyncMock | None = None,
    a2a: AsyncMock | None = None,
) -> InstructService:
    db = AsyncMock()
    svc = InstructService(db)
    if instructions is not None:
        svc._instructions = instructions
    if a2a is not None:
        svc._a2a = a2a
    return svc


def _make_subagent_service(
    *,
    instances: AsyncMock | None = None,
    agents_facade: AsyncMock | None = None,
) -> SubagentService:
    db = AsyncMock()
    svc = SubagentService(db)
    if instances is not None:
        svc._instances = instances
    if agents_facade is not None:
        svc._agents = agents_facade
    return svc


# ===========================================================================
# ApprovalService._evaluate_votes (pure logic)
# ===========================================================================


class TestEvaluateVotesSingle:
    def test_leader_approves(self) -> None:
        ap = _approval(mode=ApprovalMode.SINGLE, leader=_AGENT_A)
        votes = [_vote(_AGENT_A, True)]
        assert ApprovalService._evaluate_votes(ap, votes) is ApprovalState.APPROVED

    def test_leader_rejects(self) -> None:
        ap = _approval(mode=ApprovalMode.SINGLE, leader=_AGENT_A)
        votes = [_vote(_AGENT_A, False)]
        assert ApprovalService._evaluate_votes(ap, votes) is ApprovalState.REJECTED

    def test_non_leader_vote_ignored(self) -> None:
        ap = _approval(mode=ApprovalMode.SINGLE, leader=_AGENT_A)
        votes = [_vote(_AGENT_B, True)]
        assert ApprovalService._evaluate_votes(ap, votes) is None

    def test_leader_last_vote_wins(self) -> None:
        ap = _approval(mode=ApprovalMode.SINGLE, leader=_AGENT_A)
        votes = [_vote(_AGENT_A, True), _vote(_AGENT_A, False)]
        assert ApprovalService._evaluate_votes(ap, votes) is ApprovalState.REJECTED


class TestEvaluateVotesMajority:
    def test_majority_approves(self) -> None:
        ap = _approval(mode=ApprovalMode.MAJORITY)
        votes = [_vote(_AGENT_A, True), _vote(_AGENT_B, True), _vote(_AGENT_C, False)]
        assert ApprovalService._evaluate_votes(ap, votes) is ApprovalState.APPROVED

    def test_majority_rejects(self) -> None:
        ap = _approval(mode=ApprovalMode.MAJORITY)
        votes = [_vote(_AGENT_A, False), _vote(_AGENT_B, False), _vote(_AGENT_C, True)]
        assert ApprovalService._evaluate_votes(ap, votes) is ApprovalState.REJECTED

    def test_not_all_voted_returns_none(self) -> None:
        ap = _approval(mode=ApprovalMode.MAJORITY)
        votes = [_vote(_AGENT_A, True)]
        assert ApprovalService._evaluate_votes(ap, votes) is None

    def test_tie_broken_by_leader(self) -> None:
        d = uuid.uuid4()
        ap = _approval(
            mode=ApprovalMode.MAJORITY,
            leader=_AGENT_A,
            approvers=(_AGENT_A, _AGENT_B, _AGENT_C, d),
        )
        votes = [
            _vote(_AGENT_A, True),
            _vote(_AGENT_B, True),
            _vote(_AGENT_C, False),
            _vote(d, False),
        ]
        assert ApprovalService._evaluate_votes(ap, votes) is ApprovalState.APPROVED

    def test_tie_leader_rejects(self) -> None:
        d = uuid.uuid4()
        ap = _approval(
            mode=ApprovalMode.MAJORITY,
            leader=_AGENT_A,
            approvers=(_AGENT_A, _AGENT_B, _AGENT_C, d),
        )
        votes = [
            _vote(_AGENT_A, False),
            _vote(_AGENT_B, True),
            _vote(_AGENT_C, False),
            _vote(d, True),
        ]
        assert ApprovalService._evaluate_votes(ap, votes) is ApprovalState.REJECTED


class TestEvaluateVotesConsensus:
    def test_all_approve(self) -> None:
        ap = _approval(mode=ApprovalMode.CONSENSUS)
        votes = [_vote(_AGENT_A, True), _vote(_AGENT_B, True), _vote(_AGENT_C, True)]
        assert ApprovalService._evaluate_votes(ap, votes) is ApprovalState.APPROVED

    def test_all_reject(self) -> None:
        ap = _approval(mode=ApprovalMode.CONSENSUS)
        votes = [_vote(_AGENT_A, False), _vote(_AGENT_B, False), _vote(_AGENT_C, False)]
        assert ApprovalService._evaluate_votes(ap, votes) is ApprovalState.REJECTED

    def test_mixed_returns_none(self) -> None:
        ap = _approval(mode=ApprovalMode.CONSENSUS)
        votes = [_vote(_AGENT_A, True), _vote(_AGENT_B, False), _vote(_AGENT_C, True)]
        assert ApprovalService._evaluate_votes(ap, votes) is None

    def test_not_all_voted_returns_none(self) -> None:
        ap = _approval(mode=ApprovalMode.CONSENSUS)
        votes = [_vote(_AGENT_A, True), _vote(_AGENT_B, True)]
        assert ApprovalService._evaluate_votes(ap, votes) is None


# ===========================================================================
# ApprovalService gate lifecycle
# ===========================================================================


class TestApprovalCreateGate:
    @patch("contexts.orchestration.application.approval_service.ApprovalService._notify_and_arm", new_callable=AsyncMock)
    @patch("contexts.orchestration.application.approval_service.Publisher")
    @patch("contexts.orchestration.application.approval_service.audit.emit", new_callable=AsyncMock)
    async def test_create_gate(self, _audit, _pub_cls, _notify) -> None:
        ap = _approval()
        approvals = AsyncMock()
        approvals.insert.return_value = ap
        _pub_cls.return_value = AsyncMock()
        svc = _make_approval_service(approvals=approvals)

        config = ApprovalGateConfig(
            mode=ApprovalMode.SINGLE,
            approvers=(_AGENT_A, _AGENT_B, _AGENT_C),
            leader_agent_id=_AGENT_A,
        )
        result = await svc.create_gate(
            workflow_run_id=_RUN,
            config=config,
            chatroom_id=_ROOM,
        )

        assert result.mode is ApprovalMode.SINGLE
        _audit.assert_awaited_once()
        assert _audit.call_args[0][1].action == "approval.requested"


class TestApprovalCastVote:
    @patch("contexts.orchestration.application.approval_service.ApprovalService._enqueue_workflow_resume", new_callable=AsyncMock)
    @patch("contexts.orchestration.application.approval_service.ApprovalService._publish_resolved", new_callable=AsyncMock)
    @patch("contexts.orchestration.application.approval_service.APPROVAL_RESOLUTIONS", new_callable=MagicMock)
    @patch("contexts.orchestration.application.approval_service.audit.emit", new_callable=AsyncMock)
    async def test_cast_resolves_single(self, _audit, _metrics, _publish, _resume) -> None:
        ap = _approval(mode=ApprovalMode.SINGLE, leader=_AGENT_A)
        ballot = _vote(_AGENT_A, True)
        approvals = AsyncMock()
        approvals.get.return_value = ap
        approvals.update_state.return_value = True
        votes_repo = AsyncMock()
        votes_repo.cast.return_value = ballot
        votes_repo.list_for_approval.return_value = [ballot]
        svc = _make_approval_service(approvals=approvals, votes=votes_repo)

        result = await svc.cast_vote(
            approval_id=ap.id,
            voter_agent_id=_AGENT_A,
            vote=True,
        )

        assert result.vote is True
        approvals.update_state.assert_awaited_once()
        _resume.assert_awaited_once()

    async def test_cast_on_resolved_raises(self) -> None:
        ap = _approval(state=ApprovalState.APPROVED)
        approvals = AsyncMock()
        approvals.get.return_value = ap
        svc = _make_approval_service(approvals=approvals)

        with pytest.raises(ValueError, match="already resolved"):
            await svc.cast_vote(
                approval_id=ap.id,
                voter_agent_id=_AGENT_A,
                vote=True,
            )


class TestApprovalTimeout:
    @patch("contexts.orchestration.application.approval_service.ApprovalService._enqueue_workflow_resume", new_callable=AsyncMock)
    @patch("contexts.orchestration.application.approval_service.ApprovalService._publish_resolved", new_callable=AsyncMock)
    @patch("contexts.orchestration.application.approval_service.APPROVAL_RESOLUTIONS", new_callable=MagicMock)
    @patch("contexts.orchestration.application.approval_service.audit.emit", new_callable=AsyncMock)
    async def test_timeout_resolves_to_timeout_leader(self, _audit, _metrics, _publish, _resume) -> None:
        ap = _approval()
        approvals = AsyncMock()
        approvals.get.return_value = ap
        approvals.update_state.return_value = True
        votes_repo = AsyncMock()
        votes_repo.list_for_approval.return_value = []
        svc = _make_approval_service(approvals=approvals, votes=votes_repo)

        result = await svc.handle_timeout(ap.id)
        assert result is ApprovalState.TIMEOUT_LEADER

    async def test_timeout_already_resolved_returns_state(self) -> None:
        ap = _approval(state=ApprovalState.APPROVED)
        approvals = AsyncMock()
        approvals.get.return_value = ap
        svc = _make_approval_service(approvals=approvals)

        result = await svc.handle_timeout(ap.id)
        assert result is ApprovalState.APPROVED

    async def test_timeout_not_found_returns_none(self) -> None:
        approvals = AsyncMock()
        approvals.get.return_value = None
        svc = _make_approval_service(approvals=approvals)

        result = await svc.handle_timeout(uuid.uuid4())
        assert result is None


# ===========================================================================
# InstructService
# ===========================================================================


class TestInstructIssue:
    @patch("contexts.orchestration.application.instruct_service.INSTRUCT_CHAIN_DEPTH")
    @patch("contexts.orchestration.application.instruct_service.audit.emit", new_callable=AsyncMock)
    async def test_issue_success(self, _audit, _metric) -> None:
        instr = _instruction()
        instructions = AsyncMock()
        instructions.insert.return_value = instr
        instructions.get_chain_start_time.return_value = None
        a2a = AsyncMock()
        svc = _make_instruct_service(instructions=instructions, a2a=a2a)

        result = await svc.issue(
            issuer_agent_id=_AGENT_A,
            target_agent_id=_AGENT_B,
            payload={"task": "do"},
        )

        assert result.state is InstructionState.ISSUED
        a2a.send.assert_awaited_once()
        _audit.assert_awaited_once()

    @patch("contexts.orchestration.application.instruct_service.audit.emit", new_callable=AsyncMock)
    async def test_loop_detected(self, _audit) -> None:
        instructions = AsyncMock()
        instructions.insert.return_value = _instruction(state=InstructionState.REJECTED_LOOP)
        svc = _make_instruct_service(instructions=instructions)

        with pytest.raises(InstructLoopDetected):
            await svc.issue(
                issuer_agent_id=_AGENT_A,
                target_agent_id=_AGENT_A,
                payload={"task": "do"},
                parent_path=(),
            )

    async def test_depth_cap(self) -> None:
        svc = _make_instruct_service()

        with pytest.raises(InstructBudgetExceeded, match="chain depth"):
            await svc.issue(
                issuer_agent_id=_AGENT_A,
                target_agent_id=_AGENT_B,
                payload={"task": "do"},
                parent_path=tuple(uuid.uuid4() for _ in range(5)),
                max_chain_depth=5,
            )

    async def test_per_wakeup_count_cap(self) -> None:
        instructions = AsyncMock()
        instructions.count_issued_by_agent_since.return_value = 5
        instructions.get_chain_start_time.return_value = None
        svc = _make_instruct_service(instructions=instructions)

        with pytest.raises(InstructBudgetExceeded, match="wakeup"):
            await svc.issue(
                issuer_agent_id=_AGENT_A,
                target_agent_id=_AGENT_B,
                payload={"task": "do"},
                wakeup_started_at=_NOW,
                max_per_wakeup=5,
            )

    async def test_wall_clock_budget(self) -> None:
        chain_id = uuid.uuid4()
        instructions = AsyncMock()
        # Use a time far in the past relative to real now() so the elapsed
        # check (datetime.now(UTC) - chain_start) always exceeds 0.
        instructions.get_chain_start_time.return_value = datetime(2020, 1, 1, tzinfo=UTC)
        a2a = AsyncMock()
        svc = _make_instruct_service(instructions=instructions, a2a=a2a)

        with pytest.raises(InstructBudgetExceeded, match="elapsed"):
            await svc.issue(
                issuer_agent_id=_AGENT_A,
                target_agent_id=_AGENT_B,
                payload={"task": "do"},
                chain_id=chain_id,
                max_chain_seconds=120,
            )


class TestInstructStateTransitions:
    @patch("contexts.orchestration.application.instruct_service.audit.emit", new_callable=AsyncMock)
    async def test_mark_delivered(self, _audit) -> None:
        instructions = AsyncMock()
        svc = _make_instruct_service(instructions=instructions)

        await svc.mark_delivered(uuid.uuid4())
        instructions.update_state.assert_awaited_once()
        assert instructions.update_state.call_args[0][1] is InstructionState.DELIVERED

    @patch("contexts.orchestration.application.instruct_service.audit.emit", new_callable=AsyncMock)
    async def test_mark_completed(self, _audit) -> None:
        instructions = AsyncMock()
        svc = _make_instruct_service(instructions=instructions)

        await svc.mark_completed(uuid.uuid4())
        assert instructions.update_state.call_args[0][1] is InstructionState.COMPLETED

    @patch("contexts.orchestration.application.instruct_service.audit.emit", new_callable=AsyncMock)
    async def test_mark_failed_uses_timeout_state(self, _audit) -> None:
        instructions = AsyncMock()
        svc = _make_instruct_service(instructions=instructions)

        await svc.mark_failed(uuid.uuid4())
        assert instructions.update_state.call_args[0][1] is InstructionState.TIMEOUT
        _audit.assert_awaited_once()
        assert _audit.call_args[0][1].action == "instruct.failed"


# ===========================================================================
# SubagentService
# ===========================================================================


class TestSubagentSpawn:
    @patch("contexts.orchestration.application.subagent_service.SUBAGENT_CONCURRENCY", new_callable=MagicMock)
    @patch("contexts.orchestration.application.subagent_service.audit.emit", new_callable=AsyncMock)
    async def test_spawn_success(self, _audit, _metric) -> None:
        parent = _instance(parent_id=None)
        child = _instance(parent_id=parent.id)
        instances = AsyncMock()
        instances.get.return_value = parent
        instances.count_alive_children.return_value = 0
        instances.insert.return_value = child
        agent = MagicMock()
        agent.id = _AGENT_A
        agent.key_group_id = uuid.uuid4()
        agent.system_prompt = "be helpful"
        agent.prompt_strategy.value = "default"
        agent.model_hint.value = "auto"
        agent.context_mode.value = "full"
        agent.context_token_cap = 8000
        agents_facade = AsyncMock()
        agents_facade.get_agent.return_value = agent
        svc = _make_subagent_service(instances=instances, agents_facade=agents_facade)

        result = await svc.spawn(
            parent_instance_id=parent.id,
            parent_agent_id=_AGENT_A,
            task_description="sub-task",
        )

        assert result.parent_id == parent.id
        instances.insert.assert_awaited_once()

    async def test_depth_exceeded(self) -> None:
        grandparent = uuid.uuid4()
        parent = _instance(parent_id=grandparent)
        instances = AsyncMock()
        instances.get.return_value = parent
        svc = _make_subagent_service(instances=instances)

        with pytest.raises(SubagentDepthExceeded):
            await svc.spawn(
                parent_instance_id=parent.id,
                parent_agent_id=_AGENT_A,
                task_description="nope",
            )

    async def test_concurrency_exceeded(self) -> None:
        parent = _instance(parent_id=None)
        instances = AsyncMock()
        instances.get.return_value = parent
        instances.count_alive_children.return_value = 3
        svc = _make_subagent_service(instances=instances)

        with pytest.raises(SubagentConcurrencyExceeded):
            await svc.spawn(
                parent_instance_id=parent.id,
                parent_agent_id=_AGENT_A,
                task_description="nope",
                max_concurrent=3,
            )

    async def test_parent_not_found_raises(self) -> None:
        instances = AsyncMock()
        instances.get.return_value = None
        svc = _make_subagent_service(instances=instances)

        with pytest.raises(ValueError, match="not found"):
            await svc.spawn(
                parent_instance_id=uuid.uuid4(),
                parent_agent_id=_AGENT_A,
                task_description="nope",
            )

    async def test_concurrency_hard_cap_applied(self) -> None:
        parent = _instance(parent_id=None)
        instances = AsyncMock()
        instances.get.return_value = parent
        instances.count_alive_children.return_value = 20
        svc = _make_subagent_service(instances=instances)

        with pytest.raises(SubagentConcurrencyExceeded):
            await svc.spawn(
                parent_instance_id=parent.id,
                parent_agent_id=_AGENT_A,
                task_description="nope",
                max_concurrent=100,
            )


class TestSubagentDestroy:
    @patch("contexts.orchestration.application.subagent_service.SubagentService._fire_workflow_callback", new_callable=AsyncMock)
    @patch("contexts.orchestration.application.subagent_service.SUBAGENT_CONCURRENCY", new_callable=MagicMock)
    @patch("contexts.orchestration.application.subagent_service.audit.emit", new_callable=AsyncMock)
    async def test_destroy_success(self, _audit, _metric, _callback) -> None:
        parent = uuid.uuid4()
        inst = _instance(parent_id=parent)
        instances = AsyncMock()
        instances.get.return_value = inst
        svc = _make_subagent_service(instances=instances)

        await svc.destroy(inst.id)

        instances.destroy.assert_awaited_once()
        _callback.assert_awaited_once()
        _audit.assert_awaited_once()
        assert _audit.call_args[0][1].action == "subagent.destroyed"

    @patch("contexts.orchestration.application.subagent_service.audit.emit", new_callable=AsyncMock)
    async def test_destroy_not_found_noop(self, _audit) -> None:
        instances = AsyncMock()
        instances.get.return_value = None
        svc = _make_subagent_service(instances=instances)

        await svc.destroy(uuid.uuid4())

        instances.destroy.assert_not_awaited()


class TestSubagentInheritance:
    def test_build_inherited_context(self) -> None:
        agent = MagicMock()
        agent.id = _AGENT_A
        agent.key_group_id = uuid.uuid4()
        agent.system_prompt = "be concise"
        agent.prompt_strategy.value = "default"
        agent.model_hint.value = "auto"
        agent.context_mode.value = "full"
        agent.context_token_cap = 4000

        ctx = SubagentService._build_inherited_context(agent, "do task X")

        assert ctx["system_prompt"] == "be concise"
        assert ctx["can_create_subagent"] is False
        assert ctx["can_instruct"] is False
        assert ctx["can_approve"] is False
        assert ctx["a2a_enabled"] is False
        assert ctx["rag_config_id"] is None
        assert ctx["wakeup_config"] is None
        assert ctx["task_description"] == "do task X"
        assert ctx["parent_agent_id"] == str(_AGENT_A)


class TestSubagentEnsureRoot:
    async def test_returns_existing_root(self) -> None:
        existing = _instance()
        instances = AsyncMock()
        instances.find_alive_root_for_workflow_run.return_value = existing
        svc = _make_subagent_service(instances=instances)

        result = await svc.ensure_root_instance(
            agent_id=_AGENT_A,
            workflow_run_id=_RUN,
        )

        assert result.id == existing.id
        instances.insert.assert_not_awaited()

    async def test_creates_root_when_absent(self) -> None:
        new_root = _instance()
        instances = AsyncMock()
        instances.find_alive_root_for_workflow_run.return_value = None
        instances.insert.return_value = new_root
        svc = _make_subagent_service(instances=instances)

        result = await svc.ensure_root_instance(
            agent_id=_AGENT_A,
            workflow_run_id=_RUN,
        )

        assert result.id == new_root.id
        instances.insert.assert_awaited_once()
        insert_kwargs = instances.insert.call_args.kwargs
        assert insert_kwargs["parent_id"] is None
        assert insert_kwargs["run_context"]["synthetic_root"] is True


class TestSubagentReadHelpers:
    async def test_list_children(self) -> None:
        children = [_instance(parent_id=uuid.uuid4())]
        instances = AsyncMock()
        instances.list_alive_children.return_value = children
        svc = _make_subagent_service(instances=instances)

        result = await svc.list_children(uuid.uuid4())
        assert len(result) == 1

    async def test_cleanup_expired(self) -> None:
        instances = AsyncMock()
        instances.delete_older_than_days.return_value = 5
        svc = _make_subagent_service(instances=instances)

        count = await svc.cleanup_expired(retention_days=30)
        assert count == 5
