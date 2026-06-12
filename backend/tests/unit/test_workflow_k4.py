"""Unit tests for K.4 — workflow engine remediation.

Covers the four defects and the resume layer without a live DB/Redis:

- executor registry completeness (defect 1 — the ff19610 regression backstop);
- approval_gate config construction: field name, mode coercion, leader fold-in,
  and resume claim-key registration (defect 2);
- trigger_run project_id resolution via the workspace (defect 3);
- event-dispatch matchers + Redis/DB scan helpers, the approval→port mapping,
  and RunEngine.force_fail (defect 4).
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from contexts.workflow.application import event_dispatch as ed
from contexts.workflow.domain.models import NodeType

# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class _FakeRedis:
    """Minimal async Redis covering the surface K.4 touches."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def set(self, key, value, ex=None):
        self.kv[key] = value

    async def get(self, key):
        return self.kv.get(key)

    async def getdel(self, key):
        return self.kv.pop(key, None)

    async def delete(self, key):
        self.kv.pop(key, None)

    async def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    async def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def expire(self, key, ttl):
        return True


def _seed_wait(redis: _FakeRedis, run_id: str, node_id: str, event_type: str, match: dict) -> None:
    redis.sets.setdefault(f"wf:wait:by_event:{event_type}", set()).add(f"{run_id}:{node_id}")
    redis.kv[f"wf:wait:{run_id}:{node_id}"] = json.dumps(
        {"run_id": run_id, "node_id": node_id, "event_type": event_type, "match": match}
    )


# --------------------------------------------------------------------------- #
# Defect 1 — executor registry completeness                                    #
# --------------------------------------------------------------------------- #


def test_executor_completeness() -> None:
    """Every NodeType must resolve — the permanent guard against ff19610."""
    from contexts.workflow.application.executors import get_executor

    for node_type in NodeType:
        assert get_executor(node_type) is not None, f"no executor for {node_type}"


def test_registry_has_all_eleven() -> None:
    from contexts.workflow.application.executors.registry import _REGISTRY

    assert len(_REGISTRY) == len(list(NodeType)) == 11


# --------------------------------------------------------------------------- #
# Defect 2 — approval_gate config construction + claim key                     #
# --------------------------------------------------------------------------- #


async def test_approval_gate_builds_config_and_registers_claim(monkeypatch) -> None:
    from contexts.orchestration.domain.models import ApprovalMode
    from contexts.workflow.application.executors import approval_gate as ag
    from contexts.workflow.domain.models import NodeSpec, RunContext, StepState

    captured: dict = {}
    approval_id = uuid.uuid4()

    class _FakeFacade:
        def __init__(self, db):
            pass

        async def create_approval_gate(self, *, workflow_run_id, config, chatroom_id=None):
            captured["config"] = config
            captured["run_id"] = workflow_run_id
            return SimpleNamespace(id=approval_id)

    fake_redis = _FakeRedis()
    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _FakeFacade)
    monkeypatch.setattr("shared_kernel.auth.clients.get_redis", lambda: fake_redis)
    monkeypatch.setattr(ag, "Publisher", lambda *a, **k: SimpleNamespace(emit=AsyncMock()))

    leader, other = uuid.uuid4(), uuid.uuid4()
    node = NodeSpec(
        id="gate1",
        type=NodeType.APPROVAL_GATE,
        config={
            "mode": "majority",
            "leader_agent_id": str(leader),
            "approvers": [str(other)],  # leader intentionally NOT listed
            "timeout_seconds": 600,
            "question_template": "ok?",
        },
    )
    ctx = RunContext(run_id=uuid.uuid4(), workflow_id=uuid.uuid4(), workflow_def={}, variables={})

    outcome = await ag.execute(ctx, node, MagicMock())

    assert outcome.park is True
    assert outcome.state == StepState.RUNNING
    cfg = captured["config"]
    # mode coerced to the enum, not a raw string.
    assert cfg.mode == ApprovalMode.MAJORITY
    # leader folded into approvers (post_init requires it).
    assert leader in cfg.approvers
    assert other in cfg.approvers
    assert cfg.leader_agent_id == leader
    # resume claim key registered with (run_id, node_id).
    claim = json.loads(fake_redis.kv[f"wf:approval:{approval_id}"])
    assert claim == {"run_id": str(ctx.run_id), "node_id": "gate1"}


async def test_approval_gate_failure_returns_timeout_port(monkeypatch) -> None:
    from contexts.workflow.application.executors import approval_gate as ag
    from contexts.workflow.domain.models import NodeSpec, RunContext, StepState

    class _BoomFacade:
        def __init__(self, db):
            pass

        async def create_approval_gate(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _BoomFacade)
    monkeypatch.setattr(ag, "Publisher", lambda *a, **k: SimpleNamespace(emit=AsyncMock()))

    node = NodeSpec(
        id="g",
        type=NodeType.APPROVAL_GATE,
        config={"mode": "single", "leader_agent_id": str(uuid.uuid4()), "approvers": []},
    )
    ctx = RunContext(run_id=uuid.uuid4(), workflow_id=uuid.uuid4(), workflow_def={}, variables={})

    outcome = await ag.execute(ctx, node, MagicMock())

    assert outcome.state == StepState.FAILED
    assert outcome.port == "timeout"


# --------------------------------------------------------------------------- #
# Defect 3 — project_id resolution                                             #
# --------------------------------------------------------------------------- #


async def test_trigger_run_resolves_project_id_when_absent(monkeypatch) -> None:
    from contexts.workflow.application import workflow_service as ws

    resolved_pid = uuid.uuid4()
    wf_id = uuid.uuid4()
    started = {}

    class _FakeRepo:
        def __init__(self, db):
            pass

        async def get(self, workflow_id, include_deleted=False):
            return SimpleNamespace(definition={"nodes": []})

        async def resolve_project_id(self, workflow_id):
            return resolved_pid

    class _FakeEngine:
        def __init__(self, db):
            pass

        async def start_run(self, *, project_id, **kwargs):
            started["project_id"] = project_id
            return uuid.uuid4()

    svc = ws.WorkflowService.__new__(ws.WorkflowService)
    svc._db = MagicMock()
    svc._repo = _FakeRepo(None)
    monkeypatch.setattr(ws, "RunEngine", _FakeEngine)

    await svc.trigger_run(wf_id, trigger_payload={"trigger_type": "cron"})

    assert started["project_id"] == resolved_pid


async def test_trigger_run_raises_when_project_unresolvable(monkeypatch) -> None:
    from contexts.workflow.application import workflow_service as ws
    from contexts.workflow.domain.errors import WorkflowNotFound

    class _FakeRepo:
        def __init__(self, db):
            pass

        async def get(self, workflow_id, include_deleted=False):
            return SimpleNamespace(definition={"nodes": []})

        async def resolve_project_id(self, workflow_id):
            return None

    svc = ws.WorkflowService.__new__(ws.WorkflowService)
    svc._db = MagicMock()
    svc._repo = _FakeRepo(None)

    with pytest.raises(WorkflowNotFound):
        await svc.trigger_run(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Defect 4 — pure matchers                                                     #
# --------------------------------------------------------------------------- #


def test_matches_message_chatroom_sender_regex() -> None:
    room = str(uuid.uuid4())
    base = {"chatroom_id": room, "sender_filter": "user", "content_regex": "deploy"}
    assert ed.matches_message(base, chatroom_id=room, sender_type="user", content="please deploy")
    # wrong room
    assert not ed.matches_message(base, chatroom_id=str(uuid.uuid4()), sender_type="user", content="deploy")
    # wrong sender
    assert not ed.matches_message(base, chatroom_id=room, sender_type="agent", content="deploy")
    # regex miss
    assert not ed.matches_message(base, chatroom_id=room, sender_type="user", content="hello")


def test_matches_message_any_sender_no_regex() -> None:
    room = str(uuid.uuid4())
    m = {"chatroom_id": room}
    assert ed.matches_message(m, chatroom_id=room, sender_type="agent", content="x")


def test_matches_a2a_and_trigger() -> None:
    agent = str(uuid.uuid4())
    assert ed.matches_a2a({"target_agent_id": agent, "types": ["call"]}, target_agent_id=agent, msg_type="call")
    assert not ed.matches_a2a({"target_agent_id": agent, "types": ["call"]}, target_agent_id=agent, msg_type="notify")
    # no types → any type matches
    assert ed.matches_a2a({"target_agent_id": agent}, target_agent_id=agent, msg_type="notify")
    # trigger requires event_types membership
    assert ed.matches_a2a_trigger({"agent_id": agent, "event_types": ["instruct"]}, agent_id=agent, msg_type="instruct")
    assert not ed.matches_a2a_trigger({"agent_id": agent, "event_types": ["call"]}, agent_id=agent, msg_type="reply")


def test_matches_variable_expression() -> None:
    assert ed.matches_variable({"expression": "{{ count }} > 3"}, {"count": 5})
    assert not ed.matches_variable({"expression": "{{ count }} > 3"}, {"count": 1})
    assert not ed.matches_variable({"expression": ""}, {"count": 5})
    # a malformed expression never silently matches.
    assert not ed.matches_variable({"expression": "count > 3"}, {"count": 5})


# --------------------------------------------------------------------------- #
# Defect 4 — Redis/DB scan helpers                                             #
# --------------------------------------------------------------------------- #


async def test_find_matching_waits_filters_by_criteria() -> None:
    redis = _FakeRedis()
    room = str(uuid.uuid4())
    r1, r2 = str(uuid.uuid4()), str(uuid.uuid4())
    _seed_wait(redis, r1, "w", "message_in_room", {"chatroom_id": room})
    _seed_wait(redis, r2, "w", "message_in_room", {"chatroom_id": str(uuid.uuid4())})

    def pred(match):
        return ed.matches_message(match, chatroom_id=room, sender_type="user", content="hi")

    out = await ed.find_matching_waits(redis, "message_in_room", pred)
    assert out == [(r1, "w")]


async def test_find_matching_waits_prunes_dangling_index() -> None:
    redis = _FakeRedis()
    rid = str(uuid.uuid4())
    # index member present but claim key missing (already consumed/expired)
    redis.sets["wf:wait:by_event:a2a_message"] = {f"{rid}:n"}

    out = await ed.find_matching_waits(redis, "a2a_message", lambda m: True)
    assert out == []
    assert f"{rid}:n" not in redis.sets["wf:wait:by_event:a2a_message"]


async def test_find_run_variable_waits_scoped_to_run() -> None:
    redis = _FakeRedis()
    r1, r2 = str(uuid.uuid4()), str(uuid.uuid4())
    _seed_wait(redis, r1, "v", "variable_matches", {"expression": "x == 1"})
    _seed_wait(redis, r2, "v", "variable_matches", {"expression": "y == 2"})

    out = await ed.find_run_variable_waits(redis, r1)
    assert out == [(r1, "v", {"expression": "x == 1"})]


async def test_find_triggered_workflows_matches_predicate() -> None:
    wid = uuid.uuid4()
    room = str(uuid.uuid4())
    rows = [
        SimpleNamespace(
            id=wid,
            definition={
                "nodes": [
                    {"type": "trigger", "config": {"trigger_type": "message_received", "chatroom_id": room}},
                    {"type": "end", "config": {}},
                ]
            },
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            definition={"nodes": [{"type": "trigger", "config": {"trigger_type": "cron"}}]},
        ),
    ]

    db = MagicMock()
    result = MagicMock()
    result.all.return_value = rows
    db.execute = AsyncMock(return_value=result)

    def pred(config):
        return config.get("chatroom_id") == room

    out = await ed.find_triggered_workflows(db, "message_received", pred)
    assert out == [wid]


# --------------------------------------------------------------------------- #
# Defect 4 — approval → port mapping                                           #
# --------------------------------------------------------------------------- #


def test_approval_port_mapping() -> None:
    from app.workers.tasks.workflow import _approval_port
    from contexts.orchestration.domain.models import ApprovalState

    leader = uuid.uuid4()

    def appr(state):
        return SimpleNamespace(state=state, leader_agent_id=leader)

    assert _approval_port(appr(ApprovalState.APPROVED), []) == "approved"
    assert _approval_port(appr(ApprovalState.REJECTED), []) == "rejected"
    # timeout with no leader vote → timeout port
    assert _approval_port(appr(ApprovalState.TIMEOUT_LEADER), []) == "timeout"
    # timeout but leader voted approve → approved port
    yes = SimpleNamespace(voter_agent_id=leader, vote=True)
    assert _approval_port(appr(ApprovalState.TIMEOUT_LEADER), [yes]) == "approved"
    no = SimpleNamespace(voter_agent_id=leader, vote=False)
    assert _approval_port(appr(ApprovalState.TIMEOUT_LEADER), [no]) == "rejected"


# --------------------------------------------------------------------------- #
# Defect 4 — RunEngine.force_fail (watchdog primitive)                          #
# --------------------------------------------------------------------------- #


async def test_force_fail_marks_failed(monkeypatch) -> None:
    from contexts.workflow.application import run_engine as re_mod
    from contexts.workflow.application.run_engine import RunEngine
    from contexts.workflow.domain.models import RunState

    run_id = uuid.uuid4()
    engine = RunEngine(db=MagicMock())
    engine._runs = MagicMock()
    engine._runs.get = AsyncMock(
        return_value=SimpleNamespace(id=run_id, state=RunState.WAITING)
    )
    engine._runs.update_state = AsyncMock(return_value=True)
    engine._steps = MagicMock()
    engine._steps.cancel_pending_for_run = AsyncMock(return_value=0)
    monkeypatch.setattr(re_mod.audit, "emit", AsyncMock())
    monkeypatch.setattr(re_mod, "Publisher", lambda *a, **k: SimpleNamespace(emit=AsyncMock()))

    ok = await engine.force_fail(run_id, reason="run_max_seconds exceeded")

    assert ok is True
    engine._runs.update_state.assert_awaited_once()
    assert engine._runs.update_state.await_args.kwargs["state"] == RunState.FAILED


async def test_dispatch_enqueues_uses_underscore_defer_by() -> None:
    """A delayed enqueue must use arq's ``_defer_by`` — the bare ``defer_by``
    silently became a job arg and crashed the timeout task (K.4 fix)."""
    from datetime import timedelta

    from contexts.workflow.application.run_engine import RunEngine

    engine = RunEngine(db=MagicMock())
    engine._pending_enqueues = [("workflow_event_timeout", str(uuid.uuid4()), "n", 5000, None)]
    pool = MagicMock()
    pool.enqueue_job = AsyncMock()

    await engine.dispatch_enqueues(pool)

    pool.enqueue_job.assert_awaited_once()
    kwargs = pool.enqueue_job.await_args.kwargs
    assert "_defer_by" in kwargs
    assert "defer_by" not in kwargs
    assert kwargs["_defer_by"] == timedelta(milliseconds=5000)


async def test_force_fail_skips_terminal_run() -> None:
    from contexts.workflow.application.run_engine import RunEngine
    from contexts.workflow.domain.models import RunState

    engine = RunEngine(db=MagicMock())
    engine._runs = MagicMock()
    engine._runs.get = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), state=RunState.SUCCEEDED))
    engine._runs.update_state = AsyncMock()

    ok = await engine.force_fail(uuid.uuid4(), reason="x")

    assert ok is False
    engine._runs.update_state.assert_not_called()


# --------------------------------------------------------------------------- #
# Defect 4 — resume task control flow                                          #
# --------------------------------------------------------------------------- #


class _FakeSession:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


async def test_event_resume_short_circuits_when_already_claimed(monkeypatch) -> None:
    from app.workers.tasks import workflow as wf

    redis = _FakeRedis()  # empty — getdel returns None
    monkeypatch.setattr("shared_kernel.auth.clients.get_redis", lambda: redis)

    result = await wf.workflow_event_resume({"redis": AsyncMock()}, str(uuid.uuid4()), "n")
    assert result == "already_claimed"


async def test_resume_instruct_completed_resumes_success(monkeypatch) -> None:
    from app.workers.tasks import workflow as wf
    from contexts.orchestration.domain.models import InstructionState

    run_id, node_id, iid = str(uuid.uuid4()), "instr1", uuid.uuid4()
    redis = _FakeRedis()
    redis.kv[f"wf:instruct:{iid}"] = json.dumps({"run_id": run_id, "node_id": node_id})

    facade = SimpleNamespace(
        get_instruction=AsyncMock(
            return_value=SimpleNamespace(state=InstructionState.COMPLETED)
        )
    )
    resumed: dict = {}

    class _FakeEngine:
        def __init__(self, db):
            pass

        async def resume_at_port(self, rid, nid, port):
            resumed["args"] = (str(rid), nid, port)

        async def dispatch_enqueues(self, pool):
            pass

    db = MagicMock()
    db.commit = AsyncMock()
    monkeypatch.setattr("shared_kernel.auth.clients.get_redis", lambda: redis)
    monkeypatch.setattr("shared_kernel.db.session.async_session", lambda: _FakeSession(db))
    monkeypatch.setattr(
        "contexts.orchestration.interfaces.facade.OrchestrationFacade", lambda db: facade
    )
    monkeypatch.setattr("contexts.workflow.application.run_engine.RunEngine", _FakeEngine)
    monkeypatch.setattr("shared_kernel.audit.emit", AsyncMock())

    result = await wf.workflow_resume_instruct({"redis": AsyncMock()}, str(iid))

    assert result == "resumed:success"
    assert resumed["args"] == (run_id, node_id, "success")
    # claim key consumed.
    assert f"wf:instruct:{iid}" not in redis.kv


async def test_resume_instruct_pending_does_not_claim(monkeypatch) -> None:
    from app.workers.tasks import workflow as wf
    from contexts.orchestration.domain.models import InstructionState

    iid = uuid.uuid4()
    redis = _FakeRedis()
    redis.kv[f"wf:instruct:{iid}"] = json.dumps({"run_id": str(uuid.uuid4()), "node_id": "n"})

    facade = SimpleNamespace(
        get_instruction=AsyncMock(return_value=SimpleNamespace(state=InstructionState.DELIVERED))
    )
    db = MagicMock()
    monkeypatch.setattr("shared_kernel.auth.clients.get_redis", lambda: redis)
    monkeypatch.setattr("shared_kernel.db.session.async_session", lambda: _FakeSession(db))
    monkeypatch.setattr(
        "contexts.orchestration.interfaces.facade.OrchestrationFacade", lambda db: facade
    )

    result = await wf.workflow_resume_instruct({"redis": AsyncMock()}, str(iid))

    assert result == "pending"
    # claim key NOT consumed — a later settle can still resume.
    assert f"wf:instruct:{iid}" in redis.kv


async def test_resume_approval_retries_while_pending(monkeypatch) -> None:
    from app.workers.tasks import workflow as wf
    from contexts.orchestration.domain.models import ApprovalState

    aid = uuid.uuid4()
    redis = _FakeRedis()
    redis.kv[f"wf:approval:{aid}"] = json.dumps({"run_id": str(uuid.uuid4()), "node_id": "g"})

    facade = SimpleNamespace(
        get_approval=AsyncMock(return_value=SimpleNamespace(state=ApprovalState.PENDING))
    )
    db = MagicMock()
    pool = AsyncMock()
    monkeypatch.setattr("shared_kernel.auth.clients.get_redis", lambda: redis)
    monkeypatch.setattr("shared_kernel.db.session.async_session", lambda: _FakeSession(db))
    monkeypatch.setattr(
        "contexts.orchestration.interfaces.facade.OrchestrationFacade", lambda db: facade
    )

    result = await wf.workflow_resume_approval({"redis": pool}, str(aid), 0)

    assert result == "pending:retry"
    # re-enqueued itself with the next attempt; claim key untouched.
    pool.enqueue_job.assert_awaited_once()
    assert f"wf:approval:{aid}" in redis.kv
