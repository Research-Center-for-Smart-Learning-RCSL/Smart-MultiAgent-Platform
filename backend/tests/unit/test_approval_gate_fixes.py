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

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.workers.tasks.approvals as tasks_appr
import contexts.orchestration.application.approval_service as appr
from contexts.orchestration.domain.models import (
    Approval,
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
        def __init__(self, db, *, qdrant_url, qdrant_api_key) -> None:
            captured["engine_db"] = db

        async def run_input_turn(self, **kw):
            captured["turn_kwargs"] = kw
            return turn_result or SimpleNamespace(status="completed", text="ok", reason=None)

    @asynccontextmanager
    async def _sess():
        yield _FakeDB()

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _Facade)
    monkeypatch.setattr("contexts.agents.application.runtime.turn_engine.TurnEngine", _Engine)
    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: SimpleNamespace(qdrant=SimpleNamespace(url="http://q", api_key=None)),
    )
    monkeypatch.setattr(tasks_appr, "async_session", _sess)
    return captured


@pytest.mark.asyncio
async def test_drive_approver_turn_runs_headless_turn(monkeypatch) -> None:
    approval = _approval(ApprovalState.PENDING)
    captured = _wire_task(monkeypatch, approval)
    agent_id = uuid.uuid4()

    out = await tasks_appr.drive_approver_turn({}, str(agent_id), str(approval.id), str(uuid.uuid4()))

    assert out == "completed"
    assert captured["looked_up"] == approval.id
    kw = captured["turn_kwargs"]
    assert kw["agent_id"] == agent_id
    # The drained pending-notify supplies the cast_approval_vote tool; the
    # input just needs to point the agent at its notifications.
    assert "approval" in kw["input_text"].lower()


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

    await svc._try_resolve(approval)

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

    await svc._try_resolve(approval)

    assert ("cas", approval.id, ApprovalState.APPROVED) in effects
    assert ("audit", "approval.resolved") in effects
    resumes = [e for e in effects if e[0] == "enqueue" and e[1] == "workflow_resume_approval"]
    assert len(resumes) == 1
