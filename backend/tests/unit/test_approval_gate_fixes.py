"""Approval-gate remediation tests (post-K audit fixes).

Covers:
- ``drive_approver_turn`` worker task — drives a headless turn per approver so
  the parked approval notification is actually drained (gates no longer fall
  to the timeout port by default); skips when the gate is gone/resolved.
- Compare-and-set resolution — a timeout racing a committed vote no-ops, and
  a second concurrent resolution no-ops (no double publish/audit/resume).
- ``handle_timeout`` returns None for a missing approval.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from loguru import logger

import app.workers.tasks.approvals as tasks_appr
import app.workers.tasks.orchestration as tasks_orch
import contexts.orchestration.application.approval_service as appr
from contexts.orchestration.domain.models import (
    Approval,
    ApprovalGateConfig,
    ApprovalMode,
    ApprovalState,
    ApprovalVote,
)


def _async_return(value):
    async def _f(*_a, **_k):
        return value

    return _f


class _FakeDB:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _approval(state=ApprovalState.PENDING, *, leader=None, approvers=None):
    leader = leader or uuid.uuid4()
    approvers = approvers or (leader,)
    return Approval(
        id=uuid.uuid4(),
        workflow_run_id=uuid.uuid4(),
        mode=ApprovalMode.SINGLE,
        leader_agent_id=leader,
        approver_agent_ids=tuple(approvers),
        timeout_seconds=60,
        state=state,
        started_at=datetime.now(UTC),
        ended_at=None,
    )


def _record_effects(monkeypatch):
    """Patch Publisher / audit / enqueue / pending_notify.push onto a log."""
    effects: list[tuple] = []

    class _Publisher:
        def __init__(self, channel) -> None:
            pass

        async def emit(self, event, payload):
            effects.append(("publish", event, payload))

    async def _audit_emit(db, event):
        effects.append(("audit", event.action))

    async def _enqueue(job, *args, **kwargs):
        effects.append(("enqueue", job, args, kwargs))

    async def _push(agent_id, note):
        effects.append(("notify", agent_id, note))

    monkeypatch.setattr(appr, "Publisher", _Publisher)
    monkeypatch.setattr(appr.audit, "emit", _audit_emit)
    monkeypatch.setattr("shared_kernel.queue.enqueue", _enqueue)
    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.push", _push)
    return effects


def _bare_service(approvals):
    service = appr.ApprovalService.__new__(appr.ApprovalService)
    service._db = _FakeDB()
    service._approvals = approvals
    service._votes = MagicMock()
    return service


@pytest.mark.asyncio
async def test_create_gate_publishes_nothing_before_commit(monkeypatch) -> None:
    # F-18 (AC-1/AC-2): the create path owns no transaction, so it must perform
    # NO externally-visible effect inline — only enqueue the post-commit announce
    # job. Fails against the old code, which published, notified, armed the
    # timeout and dispatched approvers all inline.
    room_id = uuid.uuid4()
    workflow_run_id = uuid.uuid4()
    leader_id = uuid.uuid4()
    approver_id = uuid.uuid4()
    effects = _record_effects(monkeypatch)

    class _Approvals:
        async def insert(self, **kwargs):
            effects.append(("insert", kwargs))
            return SimpleNamespace(id=kwargs["id"])

    service = _bare_service(_Approvals())
    config = ApprovalGateConfig(
        mode=ApprovalMode.MAJORITY,
        leader_agent_id=leader_id,
        approvers=(leader_id, approver_id),
        timeout_seconds=60,
        question="Approve this?",
    )

    await service.create_gate(
        workflow_run_id=workflow_run_id,
        config=config,
        chatroom_id=room_id,
        node_id="gate1",
    )

    kinds = [e[0] for e in effects]
    assert "publish" not in kinds  # no WS publish before commit
    assert "notify" not in kinds  # no approver notify before commit
    enqueues = [e for e in effects if e[0] == "enqueue"]
    assert len(enqueues) == 1  # only the announce job — no timeout arm, no drive
    _, job, args, _kw = enqueues[0]
    assert job == "approval_gate_announce"
    approval_id = effects[0][1]["id"]
    assert args == (str(approval_id), str(room_id), "gate1", "Approve this?")
    # The in-transaction audit is durable with the row and is not an escape.
    assert ("audit", "approval.requested") in effects


@pytest.mark.asyncio
async def test_create_gate_fails_when_announce_enqueue_fails(monkeypatch) -> None:
    # AC-4: the announce enqueue inherits the timeout arm's load-bearing role —
    # if it raises, create_gate must propagate so the caller's transaction rolls
    # back rather than committing a row whose effects will never fire.
    _record_effects(monkeypatch)

    async def _boom_enqueue(job, *args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr("shared_kernel.queue.enqueue", _boom_enqueue)

    class _Approvals:
        async def insert(self, **kwargs):
            return SimpleNamespace(id=kwargs["id"])

    service = _bare_service(_Approvals())
    leader = uuid.uuid4()
    config = ApprovalGateConfig(
        mode=ApprovalMode.SINGLE,
        leader_agent_id=leader,
        approvers=(leader,),
        timeout_seconds=60,
    )

    with pytest.raises(RuntimeError, match="redis down"):
        await service.create_gate(workflow_run_id=uuid.uuid4(), config=config)


@pytest.mark.asyncio
async def test_announce_arms_timeout_and_drives_approvers_once_visible(monkeypatch) -> None:
    room_id = uuid.uuid4()
    leader_id = uuid.uuid4()
    approver_id = uuid.uuid4()
    approval = _approval(state=ApprovalState.PENDING, leader=leader_id, approvers=(leader_id, approver_id))
    effects = _record_effects(monkeypatch)

    class _Approvals:
        async def get(self, aid):
            return approval

    service = _bare_service(_Approvals())

    announced = await service.announce_gate(approval.id, chatroom_id=room_id, node_id="g", question="ok?")

    assert announced is True
    # AC-5: exactly one workflow-channel publish, carrying node_id + question.
    wf_publishes = [e for e in effects if e[0] == "publish" and "node_id" in e[2]]
    assert len(wf_publishes) == 1
    assert wf_publishes[0][2]["node_id"] == "g"
    assert wf_publishes[0][2]["question"] == "ok?"
    # AC-5: room publish still carries workflow_run_id (B5).
    room_publishes = [e for e in effects if e[0] == "publish" and "mode" in e[2]]
    assert len(room_publishes) == 1
    assert room_publishes[0][2]["workflow_run_id"] == str(approval.workflow_run_id)
    # Timeout armed once; one drive + one notify per approver.
    timeouts = [e for e in effects if e[0] == "enqueue" and e[1] == "approval_timeout"]
    assert len(timeouts) == 1
    drives = [e for e in effects if e[0] == "enqueue" and e[1] == "drive_approver_turn"]
    assert len(drives) == len(approval.approver_agent_ids)
    notes = [e for e in effects if e[0] == "notify"]
    assert len(notes) == len(approval.approver_agent_ids)
    assert all(n[2]["question"] == "ok?" for n in notes)
    # AC-8: no 2s dispatch delay on the approver turns — the announce job is the
    # commit barrier now.
    assert all("_defer_by" not in e[3] for e in drives)


@pytest.mark.asyncio
async def test_announce_returns_true_for_already_resolved_row(monkeypatch) -> None:
    approval = _approval(state=ApprovalState.APPROVED)
    effects = _record_effects(monkeypatch)

    class _Approvals:
        async def get(self, aid):
            return approval

    service = _bare_service(_Approvals())

    announced = await service.announce_gate(approval.id, chatroom_id=None)

    assert announced is True
    assert effects == []  # a gate resolved before announce has nothing to emit


@pytest.mark.asyncio
async def test_announce_returns_false_for_invisible_row(monkeypatch) -> None:
    effects = _record_effects(monkeypatch)

    class _Approvals:
        async def get(self, aid):
            return None

    service = _bare_service(_Approvals())

    announced = await service.announce_gate(uuid.uuid4(), chatroom_id=None)

    assert announced is False
    assert effects == []  # rolled-back / not-yet-visible gate emits nothing (AC-3)


def test_approver_turn_dispatch_delay_constant_removed() -> None:
    # AC-8: the 2s dispatch delay only approximated the commit barrier the
    # announce job now enforces properly; it must be gone.
    assert not hasattr(appr, "_APPROVER_TURN_DISPATCH_DELAY_S")


# --------------------------------------------------------------------------- #
# approval_gate_announce task — post-commit barrier + visibility retry (F-18)
# --------------------------------------------------------------------------- #


def _wire_announce(monkeypatch, announced):
    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def announce_approval_gate(self, aid, *, chatroom_id=None, node_id=None, question=None):
            return announced

    @asynccontextmanager
    async def _sess():
        yield _FakeDB()

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _Facade)
    monkeypatch.setattr(tasks_appr, "async_session", _sess)


@pytest.mark.asyncio
async def test_announce_task_returns_announced(monkeypatch) -> None:
    _wire_announce(monkeypatch, True)
    out = await tasks_appr.approval_gate_announce({}, str(uuid.uuid4()), None, "g", "q?")
    assert out == "announced"


@pytest.mark.asyncio
async def test_announce_task_retries_invisible_row(monkeypatch) -> None:
    _wire_announce(monkeypatch, False)
    enqueued: list = []

    class _Redis:
        async def enqueue_job(self, *a, **k):
            enqueued.append((a, k))

    out = await tasks_appr.approval_gate_announce({"redis": _Redis()}, str(uuid.uuid4()), None, "g", "q?")

    assert out == "retry:not_visible"
    assert len(enqueued) == 1
    assert enqueued[0][0][-1] == 1  # re-armed with attempt+1


@pytest.mark.asyncio
async def test_announce_task_gives_up_after_max_attempts(monkeypatch) -> None:
    _wire_announce(monkeypatch, False)
    enqueued: list = []

    class _Redis:
        async def enqueue_job(self, *a, **k):
            enqueued.append((a, k))

    out = await tasks_appr.approval_gate_announce(
        {"redis": _Redis()},
        str(uuid.uuid4()),
        None,
        "g",
        "q?",
        tasks_appr._NOT_VISIBLE_MAX_ATTEMPTS,
    )

    assert out == "noop:gone"
    assert enqueued == []


# --------------------------------------------------------------------------- #
# drive_approver_turn task
# --------------------------------------------------------------------------- #


def _wire_task(monkeypatch, approval, *, turn_result=None):
    """Wire the task's function-local imports with fakes; return captures."""
    captured: dict = {}

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def get_approval(self, aid):
            captured["looked_up"] = aid
            return approval

    class _Engine:
        def __init__(self, db, *, qdrant_url, qdrant_api_key, bge_reranker_url=None) -> None:
            captured["engine_db"] = db

        async def run_input_turn(self, **kw):
            captured["turn_kwargs"] = kw
            return turn_result or SimpleNamespace(
                status="completed",
                text="ok",
                reason=None,
                approvals_voted=1,
                voted_approval_ids=frozenset({approval.id}),
            )

    @asynccontextmanager
    async def _sess():
        yield _FakeDB()

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _Facade)
    monkeypatch.setattr("contexts.agents.application.runtime.turn_engine.TurnEngine", _Engine)
    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: SimpleNamespace(
            qdrant=SimpleNamespace(url="http://q", api_key=None),
            knowledge=SimpleNamespace(bge_reranker_url="http://bge:80"),
        ),
    )
    monkeypatch.setattr(tasks_appr, "async_session", _sess)
    return captured


@pytest.mark.asyncio
async def test_drive_approver_turn_runs_headless_turn(monkeypatch) -> None:
    approval = _approval(ApprovalState.PENDING)
    captured = _wire_task(monkeypatch, approval)
    agent_id = uuid.uuid4()
    room_id = uuid.uuid4()

    out = await tasks_appr.drive_approver_turn({}, str(agent_id), str(approval.id), str(room_id))

    assert out == "completed"
    assert captured["looked_up"] == approval.id
    kw = captured["turn_kwargs"]
    assert kw["agent_id"] == agent_id
    # The drained pending-notify supplies the cast_approval_vote tool; the
    # input just needs to point the agent at its notifications.
    assert "approval" in kw["input_text"].lower()
    # Q-2: the approver's authoritative room threads through so its Concept Maps
    # resolve for this turn (parsed from the carried str to a UUID).
    assert kw["chatroom_id"] == room_id


@pytest.mark.asyncio
async def test_drive_approver_turn_without_room_passes_none(monkeypatch) -> None:
    approval = _approval(ApprovalState.PENDING)
    captured = _wire_task(monkeypatch, approval)

    out = await tasks_appr.drive_approver_turn({}, str(uuid.uuid4()), str(approval.id), None)

    assert out == "completed"
    # No carried room → no Concept Map resolution (chatroom_id stays None).
    assert captured["turn_kwargs"]["chatroom_id"] is None


@pytest.mark.asyncio
async def test_drive_approver_turn_warns_when_turn_completed_without_voting(monkeypatch, caplog) -> None:
    """F-29, reporting half — a turn that reached the provider and completed
    is not the same as one that voted. Previously both logged "approver turn
    driven" at info, indistinguishable from each other; the ballot itself is
    re-armed by the engine (_settle_pending_approvals), not by this task."""
    approval = _approval(ApprovalState.PENDING)
    _wire_task(
        monkeypatch,
        approval,
        turn_result=SimpleNamespace(
            status="completed",
            text="ok",
            reason=None,
            approvals_voted=0,
            voted_approval_ids=frozenset(),
        ),
    )

    handler_id = logger.add(caplog.handler, format="{message}", level="INFO")
    try:
        with caplog.at_level(logging.INFO):
            out = await tasks_appr.drive_approver_turn({}, str(uuid.uuid4()), str(approval.id), None)
    finally:
        logger.remove(handler_id)

    assert out == "completed"
    assert any(r.levelname == "WARNING" for r in caplog.records)
    assert not any(r.levelname == "INFO" and "driven" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_drive_approver_turn_checks_its_own_gate_not_turn_wide_count(monkeypatch, caplog) -> None:
    """Review finding: an approver with two gates pending at once can vote on
    gate Y and not gate X in the same driven turn (pending_notify.drain is
    keyed only by agent_id). The job driven for gate X must warn even though
    the turn-wide approvals_voted count is nonzero, because *its* gate was
    never voted on."""
    approval_x = _approval(ApprovalState.PENDING)
    other_gate_y = uuid.uuid4()
    _wire_task(
        monkeypatch,
        approval_x,
        turn_result=SimpleNamespace(
            status="completed",
            text="ok",
            reason=None,
            approvals_voted=1,
            voted_approval_ids=frozenset({other_gate_y}),
        ),
    )

    handler_id = logger.add(caplog.handler, format="{message}", level="INFO")
    try:
        with caplog.at_level(logging.INFO):
            out = await tasks_appr.drive_approver_turn({}, str(uuid.uuid4()), str(approval_x.id), None)
    finally:
        logger.remove(handler_id)

    assert out == "completed"
    assert any(r.levelname == "WARNING" for r in caplog.records)
    assert not any(r.levelname == "INFO" and "driven" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_drive_approver_turn_skips_resolved_gate(monkeypatch) -> None:
    approval = _approval(ApprovalState.APPROVED)
    captured = _wire_task(monkeypatch, approval)

    out = await tasks_appr.drive_approver_turn({}, str(uuid.uuid4()), str(approval.id), None)

    assert out == "skipped:not_pending"
    assert "turn_kwargs" not in captured  # no provider call spent


@pytest.mark.asyncio
async def test_drive_approver_turn_retries_not_yet_visible_gate(monkeypatch) -> None:
    # A gate created inside the caller's uncommitted transaction is not yet
    # visible — the task must retry (within budget), not skip the approver.
    captured = _wire_task(monkeypatch, None)
    enqueued: list = []

    class _Redis:
        async def enqueue_job(self, *a, **k):
            enqueued.append((a, k))

    out = await tasks_appr.drive_approver_turn(
        {"redis": _Redis()}, str(uuid.uuid4()), str(uuid.uuid4()), None
    )
    assert out == "retry:not_visible"
    assert len(enqueued) == 1
    assert "turn_kwargs" not in captured  # no provider call spent


@pytest.mark.asyncio
async def test_drive_approver_turn_gives_up_after_max_attempts(monkeypatch) -> None:
    captured = _wire_task(monkeypatch, None)
    out = await tasks_appr.drive_approver_turn(
        {}, str(uuid.uuid4()), str(uuid.uuid4()), None, tasks_appr._NOT_VISIBLE_MAX_ATTEMPTS
    )
    assert out == "skipped:not_visible"
    assert "turn_kwargs" not in captured


# --------------------------------------------------------------------------- #
# approval_timeout task — retry a not-yet-visible gate row (F-31)
# --------------------------------------------------------------------------- #


def _wire_timeout(monkeypatch, state):
    """Wire the approval_timeout task's imports; return captures."""
    captured: dict = {}

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def handle_approval_timeout(self, aid, *, chatroom_id=None):
            captured["looked_up"] = aid
            captured["chatroom_id"] = chatroom_id
            return state

    @asynccontextmanager
    async def _sess():
        yield _FakeDB()

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _Facade)
    monkeypatch.setattr(tasks_orch, "async_session", _sess)
    return captured


@pytest.mark.asyncio
async def test_approval_timeout_retries_when_row_not_visible(monkeypatch) -> None:
    # A gate armed inside the caller's uncommitted transaction can fire its
    # deadline before the row is visible — the backstop must retry (within
    # budget), not abandon the gate at the first miss.
    _wire_timeout(monkeypatch, None)
    enqueued: list = []

    class _Redis:
        async def enqueue_job(self, *a, **k):
            enqueued.append((a, k))

    out = await tasks_orch.approval_timeout({"redis": _Redis()}, str(uuid.uuid4()), None)

    assert out == "retry:not_visible"
    assert len(enqueued) == 1
    # Re-armed with attempt+1 carried in the tail arg.
    assert enqueued[0][0][-1] == 1


@pytest.mark.asyncio
async def test_approval_timeout_gives_up_after_max_attempts(monkeypatch) -> None:
    _wire_timeout(monkeypatch, None)
    enqueued: list = []

    class _Redis:
        async def enqueue_job(self, *a, **k):
            enqueued.append((a, k))

    out = await tasks_orch.approval_timeout(
        {"redis": _Redis()},
        str(uuid.uuid4()),
        None,
        tasks_orch._TIMEOUT_NOT_VISIBLE_MAX_ATTEMPTS,
    )

    assert out == "noop:gone"
    assert enqueued == []  # budget spent: no further re-arm


# --------------------------------------------------------------------------- #
# Compare-and-set resolution (ApprovalService)
# --------------------------------------------------------------------------- #


def _service(monkeypatch, *, approval, votes=(), cas_wins=True):
    """ApprovalService with fake repos; returns (service, side-effect log)."""
    effects: list = []

    class _Approvals:
        async def get(self, aid):
            return approval

        async def update_state(self, aid, state):
            effects.append(("cas", aid, state))
            return cas_wins

    class _Votes:
        async def list_for_approval(self, aid):
            return list(votes)

    class _Pub:
        def __init__(self, channel) -> None:
            pass

        async def emit(self, event, payload):
            effects.append(("publish", event, payload))

    async def _audit_emit(db, event):
        effects.append(("audit", event.action))

    async def _enqueue(job, *args, **kwargs):
        effects.append(("enqueue", job, args))

    class _Metric:
        def labels(self, **kw):
            effects.append(("metric", kw))
            return self

        def inc(self):
            return None

    monkeypatch.setattr(appr.audit, "emit", _audit_emit)
    monkeypatch.setattr(appr, "Publisher", _Pub)
    monkeypatch.setattr(appr, "APPROVAL_RESOLUTIONS", _Metric())
    monkeypatch.setattr("shared_kernel.queue.enqueue", _enqueue)

    svc = appr.ApprovalService.__new__(appr.ApprovalService)
    svc._db = _FakeDB()
    svc._approvals = _Approvals()
    svc._votes = _Votes()
    return svc, effects


@pytest.mark.asyncio
async def test_handle_timeout_missing_approval_returns_none(monkeypatch) -> None:
    svc, effects = _service(monkeypatch, approval=None)
    assert await svc.handle_timeout(uuid.uuid4()) is None
    assert effects == []  # no resolution side effects for a missing gate


@pytest.mark.asyncio
async def test_handle_timeout_after_resolution_noops(monkeypatch) -> None:
    approval = _approval(ApprovalState.APPROVED)
    svc, effects = _service(monkeypatch, approval=approval)

    state = await svc.handle_timeout(approval.id)

    assert state == ApprovalState.APPROVED
    assert effects == []  # already resolved before we even read: pure no-op


@pytest.mark.asyncio
async def test_handle_timeout_loses_cas_race_noops(monkeypatch) -> None:
    # Read sees PENDING, but a vote commits APPROVED before the CAS lands —
    # the WHERE state='pending' guard loses and timeout must not publish.
    approval = _approval(ApprovalState.PENDING)
    svc, effects = _service(monkeypatch, approval=approval, cas_wins=False)

    state = await svc.handle_timeout(approval.id)

    # Returns the (re-read) current state; with our static fake that's the
    # same object, the key assertion is the absence of side effects.
    assert state == approval.state
    kinds = [e[0] for e in effects]
    assert "publish" not in kinds
    assert "audit" not in kinds
    assert "enqueue" not in kinds
    assert kinds == ["cas"]


@pytest.mark.asyncio
async def test_handle_timeout_wins_cas_publishes_and_resumes(monkeypatch) -> None:
    approval = _approval(ApprovalState.PENDING)
    svc, effects = _service(monkeypatch, approval=approval, cas_wins=True)

    state = await svc.handle_timeout(approval.id)

    assert state == ApprovalState.TIMEOUT_LEADER
    kinds = [e[0] for e in effects]
    assert "publish" in kinds
    assert ("audit", "approval.resolved") in effects
    resumes = [e for e in effects if e[0] == "enqueue" and e[1] == "workflow_resume_approval"]
    assert len(resumes) == 1


@pytest.mark.asyncio
async def test_try_resolve_second_resolution_noops(monkeypatch) -> None:
    # Two concurrent votes both evaluate to a decision; only the CAS winner
    # publishes/audits/resumes — the loser must back off entirely.
    leader = uuid.uuid4()
    approval = _approval(ApprovalState.PENDING, leader=leader)
    vote = ApprovalVote(
        approval_id=approval.id,
        voter_agent_id=leader,
        vote=True,
        rationale=None,
        cast_at=datetime.now(UTC),
    )
    svc, effects = _service(monkeypatch, approval=approval, votes=[vote], cas_wins=False)

    state = await svc._resolve_state(approval)
    if state is not None:
        await svc._emit_resolution_effects(approval, state)

    kinds = [e[0] for e in effects]
    assert kinds == ["cas"]  # lost the race: nothing published, nothing enqueued


@pytest.mark.asyncio
async def test_try_resolve_winner_publishes_once(monkeypatch) -> None:
    leader = uuid.uuid4()
    approval = _approval(ApprovalState.PENDING, leader=leader)
    vote = ApprovalVote(
        approval_id=approval.id,
        voter_agent_id=leader,
        vote=True,
        rationale=None,
        cast_at=datetime.now(UTC),
    )
    svc, effects = _service(monkeypatch, approval=approval, votes=[vote], cas_wins=True)

    state = await svc._resolve_state(approval)
    if state is not None:
        await svc._emit_resolution_effects(approval, state)

    assert ("cas", approval.id, ApprovalState.APPROVED) in effects
    assert ("audit", "approval.resolved") in effects
    resumes = [e for e in effects if e[0] == "enqueue" and e[1] == "workflow_resume_approval"]
    assert len(resumes) == 1
