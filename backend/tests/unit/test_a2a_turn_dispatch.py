"""K.3 Pass 2 — A2A turn dispatch + approval participation.

Covers the headless turn path, the A2A handler's call/instruct/notify branches,
the cast_approval_vote tool, the pending-notification context drain, and the
pending_notify Redis store. The synchronous round trip over real streams is the
compose-backed K.7 wiring tier; here we pin the branch logic with fakes.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import contexts.agents.application.runtime.tool_registry as tr
import contexts.agents.application.runtime.turn_engine as te
import contexts.orchestration.application.a2a_handler as h
import contexts.orchestration.infrastructure.pending_notify as pn
from contexts.orchestration.domain.models import A2AEnvelope, A2AMessageType


def _async_return(value):
    async def _f(*_a, **_k):
        return value

    return _f


class _FakeDB:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def flush(self) -> None:
        return None


def _agent():
    return SimpleNamespace(
        id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        system_prompt="prompt",
        prompt_strategy=SimpleNamespace(value="full"),
        model_hint=SimpleNamespace(value="claude"),
        model_id=None,
    )


# --------------------------------------------------------------------------- #
# run_input_turn (headless)
# --------------------------------------------------------------------------- #


def _wire_engine(monkeypatch, agent, *, drain=None):
    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def get_agent(self, aid):
            return agent

    monkeypatch.setattr(te, "AgentsFacade", _Facade)
    monkeypatch.setattr(
        te,
        "assemble",
        lambda sp, *, strategy, provider_supports_tools: SimpleNamespace(text="SYS"),
    )
    monkeypatch.setattr(
        "contexts.orchestration.infrastructure.pending_notify.drain",
        _async_return(drain if drain is not None else []),
    )
    monkeypatch.setattr(te, "build_registry", lambda *a, **k: SimpleNamespace())


@pytest.mark.asyncio
async def test_run_input_turn_headless_completed(monkeypatch) -> None:
    agent = _agent()
    _wire_engine(monkeypatch, agent)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]
    engine._router = object()  # type: ignore[attr-defined]

    captured: dict = {}

    async def _fake_stream(**kw):
        captured.update(kw)
        return ("hello from agent", 0)

    async def _noop_audit(*a, **k):
        return None

    engine._stream_with_tools = _fake_stream  # type: ignore[attr-defined]
    engine._audit = _noop_audit  # type: ignore[attr-defined]

    result = await engine.run_input_turn(agent_id=agent.id, input_text="hi")

    assert result.status == "completed"
    assert result.text == "hello from agent"
    # Headless: no room, no chatroom — no WS stream, no persistence.
    assert captured["room"] is None
    assert captured["chatroom_id"] is None
    assert captured["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_run_input_turn_agent_gone(monkeypatch) -> None:
    _wire_engine(monkeypatch, None)
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]
    engine._router = object()  # type: ignore[attr-defined]
    result = await engine.run_input_turn(agent_id=uuid.uuid4(), input_text="hi")
    assert result.status == "skipped"
    assert result.reason == "agent_gone"


# --------------------------------------------------------------------------- #
# _pending_context_and_tools
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pending_context_adds_approval_tool(monkeypatch) -> None:
    approval_id = uuid.uuid4()
    room_id = uuid.uuid4()
    notes = [
        {
            "kind": "approval_request",
            "approval_id": str(approval_id),
            "mode": "majority",
            "chatroom_id": str(room_id),
        },
        {"kind": "notify", "from_agent": "x", "payload": {"a": 1}},
    ]
    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.drain", _async_return(notes))
    sentinel = SimpleNamespace(name="cast_approval_vote")
    seen: dict = {}

    def _build(db, *, agent_id, allowed_approvals):
        seen["allowed"] = dict(allowed_approvals)
        return sentinel

    monkeypatch.setattr(te, "build_cast_approval_vote_tool", _build)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = object()  # type: ignore[attr-defined]
    block, tools, _notes = await engine._pending_context_and_tools(_agent())

    assert block is not None
    assert str(approval_id) in block
    assert tools == [sentinel]
    # Tool scoped to exactly the pending gate, carrying its originating room.
    assert seen["allowed"] == {approval_id: room_id}


@pytest.mark.asyncio
async def test_pending_context_empty(monkeypatch) -> None:
    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.drain", _async_return([]))
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = object()  # type: ignore[attr-defined]
    block, tools, _notes = await engine._pending_context_and_tools(_agent())
    assert block is None
    assert tools == []


# --------------------------------------------------------------------------- #
# cast_approval_vote tool
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cast_approval_vote_records(monkeypatch) -> None:
    approval_id, agent_id = uuid.uuid4(), uuid.uuid4()
    captured: dict = {}

    room_id = uuid.uuid4()

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def cast_approval_vote(self, *, approval_id, voter_agent_id, vote, rationale, chatroom_id):
            captured.update(
                approval_id=approval_id,
                voter=voter_agent_id,
                vote=vote,
                rationale=rationale,
                chatroom_id=chatroom_id,
            )
            return SimpleNamespace(vote=vote)

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _Facade)
    tool = tr.build_cast_approval_vote_tool(
        object(), agent_id=agent_id, allowed_approvals={approval_id: room_id}
    )

    ok = await tool.invoke({"approval_id": str(approval_id), "vote": True, "rationale": "lgtm"})
    assert not ok.is_error
    assert captured["vote"] is True
    assert captured["voter"] == agent_id
    assert captured["rationale"] == "lgtm"
    # The gate's originating chatroom is threaded through to resolution.
    assert captured["chatroom_id"] == room_id


@pytest.mark.asyncio
async def test_cast_approval_vote_rejects_unscoped_and_bad_id(monkeypatch) -> None:
    class _Facade:
        def __init__(self, db) -> None:
            raise AssertionError("must not reach the service for an invalid gate")

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _Facade)
    tool = tr.build_cast_approval_vote_tool(
        object(), agent_id=uuid.uuid4(), allowed_approvals={uuid.uuid4(): None}
    )
    not_allowed = await tool.invoke({"approval_id": str(uuid.uuid4()), "vote": True})
    assert not_allowed.is_error
    bad = await tool.invoke({"approval_id": "not-a-uuid", "vote": True})
    assert bad.is_error


# --------------------------------------------------------------------------- #
# a2a_handler dispatch
# --------------------------------------------------------------------------- #


def _env(type_, payload, to_agent=None):
    return A2AEnvelope(
        id=uuid.uuid4(),
        from_agent=uuid.uuid4(),
        to_agent=to_agent or str(uuid.uuid4()),
        workflow_run_id=None,
        type=type_,
        payload=payload,
        correlation_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_handle_call_delivers_reply(monkeypatch) -> None:
    delivered: dict = {}

    async def _deliver(cid, env):
        delivered["cid"], delivered["env"] = cid, env

    monkeypatch.setattr(h.a2a_rendezvous, "deliver_reply", _deliver)
    monkeypatch.setattr(
        h, "_run_turn", _async_return(SimpleNamespace(status="completed", text="ANSWER", reason=None))
    )

    env = _env(A2AMessageType.CALL, {"input": "do x"})
    await h.handle_envelope(env)

    assert delivered["cid"] == env.correlation_id
    assert delivered["env"]["reply"] == "ANSWER"  # top-level for agent_invocation
    assert delivered["env"]["payload"]["output"] == "ANSWER"
    assert delivered["env"]["to_agent"] == str(env.from_agent)


@pytest.mark.asyncio
async def test_handle_call_failed_delivers_error(monkeypatch) -> None:
    delivered: dict = {}

    async def _deliver(cid, env):
        delivered["env"] = env

    monkeypatch.setattr(h.a2a_rendezvous, "deliver_reply", _deliver)
    monkeypatch.setattr(
        h, "_run_turn", _async_return(SimpleNamespace(status="failed", text="", reason="boom"))
    )

    await h.handle_envelope(_env(A2AMessageType.CALL, {"input": "x"}))
    assert h.a2a_rendezvous.A2A_ERROR_KEY in delivered["env"]["payload"]


@pytest.mark.asyncio
async def test_handle_instruct_marks_states(monkeypatch) -> None:
    calls: list = []

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def mark_instruct_delivered(self, iid):
            calls.append(("delivered", iid))

        async def mark_instruct_completed(self, iid):
            calls.append(("completed", iid))

        async def mark_instruct_timeout(self, iid):
            calls.append(("timeout", iid))

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _Facade)

    @asynccontextmanager
    async def _sess():
        yield _FakeDB()

    monkeypatch.setattr(h, "async_session", _sess)
    monkeypatch.setattr(
        h, "_run_turn_with_db", _async_return(SimpleNamespace(status="completed", text="x", reason=None))
    )

    iid = uuid.uuid4()
    await h.handle_envelope(_env(A2AMessageType.INSTRUCT, {"instruction_id": str(iid), "input": "go"}))

    assert ("delivered", iid) in calls
    assert ("completed", iid) in calls
    assert ("timeout", iid) not in calls


@pytest.mark.asyncio
async def test_handle_instruct_failed_turn_marks_failed(monkeypatch) -> None:
    calls: list = []

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def mark_instruct_delivered(self, iid):
            calls.append(("delivered", iid))

        async def mark_instruct_completed(self, iid):
            calls.append(("completed", iid))

        async def mark_instruct_timeout(self, iid):
            calls.append(("timeout", iid))

    class _Instruct:
        def __init__(self, db) -> None:
            pass

        async def mark_failed(self, iid):
            calls.append(("failed", iid))

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _Facade)
    monkeypatch.setattr("contexts.orchestration.application.instruct_service.InstructService", _Instruct)

    @asynccontextmanager
    async def _sess():
        yield _FakeDB()

    monkeypatch.setattr(h, "async_session", _sess)
    monkeypatch.setattr(
        h, "_run_turn_with_db", _async_return(SimpleNamespace(status="failed", text="", reason="x"))
    )

    iid = uuid.uuid4()
    await h.handle_envelope(_env(A2AMessageType.INSTRUCT, {"instruction_id": str(iid), "input": "go"}))
    # A provider/turn failure is recorded as a failure, not misfiled as a
    # deadline timeout.
    assert ("failed", iid) in calls
    assert ("completed", iid) not in calls
    assert ("timeout", iid) not in calls


@pytest.mark.asyncio
async def test_run_turn_with_db_passes_parent_agent_id(monkeypatch) -> None:
    captured: dict = {}

    class _Engine:
        def __init__(self, db, *, qdrant_url, qdrant_api_key) -> None:
            pass

        async def run_input_turn(self, **kw):
            captured.update(kw)
            return SimpleNamespace(status="completed", text="ok", reason=None)

    monkeypatch.setattr("contexts.agents.application.runtime.turn_engine.TurnEngine", _Engine)
    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: SimpleNamespace(qdrant=SimpleNamespace(url="http://q", api_key=None)),
    )

    env = _env(A2AMessageType.CALL, {"input": "x"})
    await h._run_turn_with_db(_FakeDB(), uuid.UUID(env.to_agent), env)

    # Usage attribution: the calling agent rides through as parent_agent_id.
    assert captured["parent_agent_id"] == uuid.UUID(str(env.from_agent))


@pytest.mark.asyncio
async def test_run_turn_with_db_tolerates_non_uuid_sender(monkeypatch) -> None:
    captured: dict = {}

    class _Engine:
        def __init__(self, db, *, qdrant_url, qdrant_api_key) -> None:
            pass

        async def run_input_turn(self, **kw):
            captured.update(kw)
            return SimpleNamespace(status="completed", text="ok", reason=None)

    monkeypatch.setattr("contexts.agents.application.runtime.turn_engine.TurnEngine", _Engine)
    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: SimpleNamespace(qdrant=SimpleNamespace(url="http://q", api_key=None)),
    )

    env = A2AEnvelope(
        id=uuid.uuid4(),
        from_agent="system",  # not a UUID — must not break the turn
        to_agent=str(uuid.uuid4()),
        workflow_run_id=None,
        type=A2AMessageType.CALL,
        payload={"input": "x"},
        correlation_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
    )
    await h._run_turn_with_db(_FakeDB(), uuid.UUID(env.to_agent), env)
    assert captured["parent_agent_id"] is None


@pytest.mark.asyncio
async def test_handle_notify_parks_notification(monkeypatch) -> None:
    pushed: list = []

    async def _push(agent_id, payload):
        pushed.append((agent_id, payload))

    monkeypatch.setattr(h.pending_notify, "push", _push)
    to = uuid.uuid4()
    await h.handle_envelope(_env(A2AMessageType.NOTIFY, {"hello": "world"}, to_agent=str(to)))

    assert pushed[0][0] == to
    assert pushed[0][1]["kind"] == "notify"
    assert pushed[0][1]["payload"] == {"hello": "world"}


# --------------------------------------------------------------------------- #
# pending_notify store
# --------------------------------------------------------------------------- #


class _FakePipe:
    def __init__(self, store) -> None:
        self._store = store
        self._ops: list = []

    def rpush(self, k, v):
        self._ops.append(("rpush", k, v))

    def ltrim(self, k, a, b):
        self._ops.append(("ltrim", k, a, b))

    def expire(self, k, t):
        self._ops.append(("expire", k, t))

    def lrange(self, k, a, b):
        self._ops.append(("lrange", k, a, b))

    def delete(self, k):
        self._ops.append(("delete", k))

    async def execute(self):
        results: list = []
        for op in self._ops:
            kind = op[0]
            if kind == "rpush":
                self._store.setdefault(op[1], []).append(op[2])
                results.append(len(self._store[op[1]]))
            elif kind == "ltrim":
                lst = self._store.get(op[1], [])
                n = len(lst)
                start = op[2] if op[2] >= 0 else max(0, n + op[2])
                stop = op[3] if op[3] >= 0 else n + op[3]
                self._store[op[1]] = lst[start : stop + 1]
                results.append("OK")
            elif kind == "lrange":
                results.append(list(self._store.get(op[1], [])))
            elif kind == "delete":
                self._store.pop(op[1], None)
                results.append(1)
            else:  # expire
                results.append(1)
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict = {}

    def pipeline(self, transaction=False):
        return _FakePipe(self.store)


@pytest.mark.asyncio
async def test_pending_notify_roundtrip(monkeypatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(pn, "get_redis", lambda: fake)
    aid = uuid.uuid4()

    await pn.push(aid, {"kind": "notify", "x": 1})
    await pn.push(aid, {"kind": "approval_request", "approval_id": "a"})

    out = await pn.drain(aid)
    assert [n["kind"] for n in out] == ["notify", "approval_request"]
    # Drained queue is empty on the next read.
    assert await pn.drain(aid) == []


# --------------------------------------------------------------------------- #
# ApprovalService gate-open hook (notify approvers + arm timeout)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_notify_and_arm_notifies_and_schedules(monkeypatch) -> None:
    import contexts.orchestration.application.approval_service as appr
    from contexts.orchestration.domain.models import ApprovalGateConfig, ApprovalMode

    leader, other = uuid.uuid4(), uuid.uuid4()
    config = ApprovalGateConfig(
        mode=ApprovalMode.MAJORITY,
        approvers=(leader, other),
        leader_agent_id=leader,
        timeout_seconds=120,
        question="Deploy v2 to production?",
    )
    approval_id, run_id, room_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    pushed: list = []

    async def _push(agent_id, payload):
        pushed.append((agent_id, payload))

    enq: list = []

    async def _enqueue(job, *args, **kwargs):
        enq.append((job, args, kwargs))

    # Patch where _notify_and_arm imports them (function-local imports resolve
    # against the source modules).
    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.push", _push)
    monkeypatch.setattr("shared_kernel.queue.enqueue", _enqueue)

    svc = appr.ApprovalService.__new__(appr.ApprovalService)
    await svc._notify_and_arm(
        approval_id=approval_id,
        config=config,
        workflow_run_id=run_id,
        chatroom_id=room_id,
    )

    # Every approver got an approval_request carrying the gate id + room +
    # the question being decided.
    assert {p[0] for p in pushed} == {leader, other}
    for _aid, note in pushed:
        assert note["kind"] == "approval_request"
        assert note["approval_id"] == str(approval_id)
        assert note["chatroom_id"] == str(room_id)
        assert note["question"] == "Deploy v2 to production?"
    # One turn-driving job per approver — without it the parked notification
    # is never drained and every gate falls to the timeout port.
    drives = [(j, a) for j, a, _k in enq if j == "drive_approver_turn"]
    assert {a[0] for _j, a in drives} == {str(leader), str(other)}
    for _j, args in drives:
        assert args[1] == str(approval_id)
        assert args[2] == str(room_id)
    # The timeout was armed as a deferred job for this gate.
    timeouts = [(j, a, k) for j, a, k in enq if j == "approval_timeout"]
    assert len(timeouts) == 1
    _job, args, kwargs = timeouts[0]
    assert args[0] == str(approval_id)
    assert args[1] == str(room_id)
    assert "_defer_by" in kwargs
