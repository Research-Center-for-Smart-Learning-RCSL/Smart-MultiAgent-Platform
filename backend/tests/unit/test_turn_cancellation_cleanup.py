"""A turn must clean up after itself when its job is killed (F-8, F-18).

`job_timeout` cancels the task, and `CancelledError` inherits `BaseException`,
so neither `_run_locked`'s `except Exception` nor `run_turn`'s post-loop drain
used to run: the room stayed "thinking", no `agent.turn_failed` was audited,
drained notifications were lost and the coalesced trigger was stranded for its
full hour of TTL.

See docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md (C1, C3), R3.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from arq.worker import Function

import contexts.agents.application.runtime.turn_engine as te
from contexts.conversation.domain.models import ChatroomAgentRole
from contexts.skills.application.binding_service import BoundSet

_NOW = datetime(2026, 7, 31, tzinfo=UTC)


# ---------------------------------------------------------------------------
# C3 — the job registration that makes the cleanup load-bearing
# ---------------------------------------------------------------------------


def _entry(name: str) -> object:
    from app.workers.main import WorkerSettings

    for fn in WorkerSettings.functions:
        if (getattr(fn, "name", None) or fn.__name__) == name:
            return fn
    raise AssertionError(f"{name} is not registered on WorkerSettings")


def test_wakeup_agent_is_registered_without_retries() -> None:
    """AC-4: a turn is not retry-safe, so its job must never be re-run."""
    entry = _entry("wakeup_agent")
    assert isinstance(entry, Function), "wakeup_agent must be registered via arq's func(...)"
    assert entry.max_tries == 1


def test_wakeup_agent_has_a_scoped_timeout_with_lock_headroom() -> None:
    """AC-4: the timeout is scoped to this lane and sized against the lock TTL.

    The relation is headroom, not tightness (Q-9): the heartbeat-refreshed,
    fail-closed turn lock is the single-writer authority, and one provider
    stream read alone may take as long as the TTL.
    """
    from app.workers.tasks.orchestration import WAKEUP_TURN_TIMEOUT_S
    from contexts.agents.infrastructure.turn_lock import DEFAULT_TURN_TTL_S

    entry = _entry("wakeup_agent")
    assert isinstance(entry, Function)
    assert entry.timeout_s == WAKEUP_TURN_TIMEOUT_S
    assert WAKEUP_TURN_TIMEOUT_S > DEFAULT_TURN_TTL_S


# ---------------------------------------------------------------------------
# C1 — the cleanup itself
# ---------------------------------------------------------------------------


class _FakeSavepoint:
    async def __aenter__(self) -> _FakeSavepoint:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class _FakeDB:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def begin_nested(self) -> _FakeSavepoint:
        return _FakeSavepoint()


class _PublisherSpy:
    emitted: ClassVar[list[tuple[str, str, dict]]] = []

    def __init__(self, channel: str) -> None:
        self._channel = channel

    async def emit(self, event: str, payload: dict) -> None:
        _PublisherSpy.emitted.append((self._channel, event, payload))


def _agent() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        system_prompt="prompt",
        model_hint=SimpleNamespace(value="claude"),
        model_id=None,
        context_mode=SimpleNamespace(value="general"),
        context_token_cap=None,
        effort=None,
        temperature=None,
        top_p=None,
        seed=None,
    )


class _Trace:
    """What the cleanup actually did, for the assertions below."""

    def __init__(self) -> None:
        self.audits: list[tuple[str, dict]] = []
        self.requeued: list[tuple[list[dict], set[uuid.UUID]]] = []
        self.compact_restored: list[uuid.UUID] = []
        self.settled = 0


def _wire_engine(monkeypatch: pytest.MonkeyPatch, agent: SimpleNamespace, *, note: dict[str, Any]):
    """Wire a full `_run_locked` pass for a NORMAL binding down to the provider
    stream, with the four cleanup steps spied rather than stubbed away."""
    _PublisherSpy.emitted = []
    trace = _Trace()
    monkeypatch.setattr(te, "Publisher", _PublisherSpy)

    class _AgentsFacade:
        def __init__(self, db: object) -> None:
            pass

        async def get_agent(self, aid: uuid.UUID) -> SimpleNamespace:
            return agent

        async def list_agent_tools(self, aid: uuid.UUID) -> list[Any]:
            return []

    monkeypatch.setattr(te, "AgentsFacade", _AgentsFacade)

    class _BindingRepo:
        def __init__(self, db: object) -> None:
            pass

        async def role_of(self, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> ChatroomAgentRole:
            return ChatroomAgentRole.NORMAL

    monkeypatch.setattr(te, "ChatroomAgentRepository", _BindingRepo)

    class _KeysFacade:
        def __init__(self, db: object) -> None:
            pass

        async def get_key_group(self, kgid: uuid.UUID) -> SimpleNamespace:
            return SimpleNamespace(project_id=agent.project_id)

        async def has_carried_provider_in_group(self, kgid: uuid.UUID, provider: object) -> bool:
            return True

    monkeypatch.setattr(te, "KeysFacade", _KeysFacade)

    class _MessageService:
        def __init__(self, db: object) -> None:
            pass

        async def send_agent(self, **kw: Any) -> SimpleNamespace:
            return SimpleNamespace(id=uuid.uuid4(), created_at=_NOW)

    monkeypatch.setattr(te, "MessageService", _MessageService)

    class _SkillsFacade:
        def __init__(self, db: object) -> None:
            pass

        async def resolve_bound_set(self, **kw: Any) -> BoundSet:
            return BoundSet(skills=())

        @staticmethod
        def render_index(skills: object) -> str:
            return ""

    monkeypatch.setattr(te, "SkillsFacade", _SkillsFacade)
    monkeypatch.setattr(te, "build_registry", lambda *a, **k: SimpleNamespace(specs=lambda: []))

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]
    engine._compact_forced_rooms = {}  # type: ignore[attr-defined]

    async def _noop(*a: Any, **k: Any) -> None:
        return None

    async def _true(*a: Any, **k: Any) -> bool:
        return True

    async def _none(*a: Any, **k: Any) -> None:
        return None

    async def _empty_list(*a: Any, **k: Any) -> list[Any]:
        return []

    async def _history(*a: Any, **k: Any) -> list[Any]:
        return [
            SimpleNamespace(
                role="user", content="hello", sender_id=uuid.uuid4(), id=uuid.uuid4(), token_count=2
            )
        ]

    async def _labels(*a: Any, **k: Any) -> tuple[dict, dict]:
        return {}, {}

    async def _no_staging(*a: Any, **k: Any) -> tuple[None, list[Any]]:
        return None, []

    async def _pending(*a: Any, **k: Any) -> tuple[None, list[Any], list[dict], set[uuid.UUID]]:
        return None, [], [note], set()

    async def _audit(agent_: object, room: uuid.UUID, action: str, extra: dict) -> None:
        trace.audits.append((action, extra))

    async def _requeue(agent_: object, notes: list[dict], *, voted: set[uuid.UUID] | None = None) -> None:
        trace.requeued.append((notes, voted or set()))

    async def _restore(room: uuid.UUID) -> None:
        trace.compact_restored.append(room)

    async def _settle(*a: Any, **k: Any) -> None:
        trace.settled += 1

    engine._audit = _audit  # type: ignore[attr-defined]
    engine._turn_rate_allowed = _true  # type: ignore[attr-defined]
    engine._assemble_history = _history  # type: ignore[attr-defined]
    engine._participant_labels = _labels  # type: ignore[attr-defined]
    engine._rag_context = _none  # type: ignore[attr-defined]
    engine._graphrag_context = _none  # type: ignore[attr-defined]
    engine._knowmap_context = _none  # type: ignore[attr-defined]
    engine._activity_context = _none  # type: ignore[attr-defined]
    engine._pending_context_and_tools = _pending  # type: ignore[attr-defined]
    engine._builtin_tools = _empty_list  # type: ignore[attr-defined]
    engine._resolve_trigger_attachments = _none  # type: ignore[attr-defined]
    engine._stage_workspace_inputs = _no_staging  # type: ignore[attr-defined]
    engine._model_attachment_blocks = _empty_list  # type: ignore[attr-defined]
    engine._provider_message = (  # type: ignore[attr-defined]
        lambda hm, aid, an, un, attachment_blocks=None: {"role": "user", "content": hm.content}
    )
    engine._dispatch_agent_message_signal = _noop  # type: ignore[attr-defined]
    engine._dispatch_agent_reply_wakeups = _noop  # type: ignore[attr-defined]
    engine._persist_artifacts = _noop  # type: ignore[attr-defined]
    engine._requeue_notifications = _requeue  # type: ignore[attr-defined]
    engine._restore_compact_flag = _restore  # type: ignore[attr-defined]
    engine._settle_pending_approvals = _settle  # type: ignore[attr-defined]

    return engine, trace


async def _run_cancelled(engine: Any, room: uuid.UUID, agent: SimpleNamespace) -> None:
    async def _cancel(**kw: Any) -> te.ToolLoopOutcome:
        raise asyncio.CancelledError

    engine._stream_with_tools = _cancel
    with pytest.raises(asyncio.CancelledError):
        await engine._run_locked(
            agent_id=agent.id,
            chatroom_id=room,
            trigger="every_n_messages",
            parent_agent_id=None,
            input_text=None,
            request_id=None,
            trigger_message_id=None,
        )


class TestCancelledTurnRunsItsCleanup:
    """AC-2. Each of these fails against the pre-C1 `except Exception`."""

    async def test_room_is_not_left_thinking(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent, room = _agent(), uuid.uuid4()
        engine, _ = _wire_engine(monkeypatch, agent, note={"kind": "notify"})

        await _run_cancelled(engine, room, agent)

        finished = [p for _, e, p in _PublisherSpy.emitted if e == "agent.finished"]
        assert finished == [{"error": "cancelled", "agent_id": str(agent.id)}]

    async def test_turn_failed_is_audited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent, room = _agent(), uuid.uuid4()
        engine, trace = _wire_engine(monkeypatch, agent, note={"kind": "notify"})

        await _run_cancelled(engine, room, agent)

        assert ("agent.turn_failed", {"error": "cancelled"}) in trace.audits

    async def test_drained_notifications_are_restored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent, room = _agent(), uuid.uuid4()
        note = {"kind": "notify", "body": "unseen"}
        engine, trace = _wire_engine(monkeypatch, agent, note=note)

        await _run_cancelled(engine, room, agent)

        assert trace.requeued == [([note], set())]

    async def test_compact_flag_is_re_armed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent, room = _agent(), uuid.uuid4()
        engine, trace = _wire_engine(monkeypatch, agent, note={"kind": "notify"})

        await _run_cancelled(engine, room, agent)

        assert trace.compact_restored == [room]

    async def test_the_cancellation_still_reaches_arq(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The cleanup delays the cancellation; it must never swallow it, or a
        killed job would be recorded as a successful turn."""
        agent, room = _agent(), uuid.uuid4()
        engine, _ = _wire_engine(monkeypatch, agent, note={"kind": "notify"})

        # _run_cancelled asserts the raise; this pins the session was rolled back
        # rather than left mid-transaction for the next user of the session.
        await _run_cancelled(engine, room, agent)

        assert engine._db.rollbacks == 1


class TestSuccessfulTurnIsNotFinalizedAsFailed:
    async def test_no_turn_failed_audit_on_the_success_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent, room = _agent(), uuid.uuid4()
        engine, trace = _wire_engine(monkeypatch, agent, note={"kind": "notify"})

        async def _ok(**kw: Any) -> te.ToolLoopOutcome:
            return te.ToolLoopOutcome(text="hi", rounds=0)

        engine._stream_with_tools = _ok

        result = await engine._run_locked(
            agent_id=agent.id,
            chatroom_id=room,
            trigger="every_n_messages",
            parent_agent_id=None,
            input_text=None,
            request_id=None,
            trigger_message_id=None,
        )

        assert result.status == "completed"
        assert [a for a, _ in trace.audits if a == "agent.turn_failed"] == []
        assert trace.requeued == []
        assert trace.compact_restored == []


class TestCancelledTurnDrainsItsQueuedTrigger:
    """AC-2's last clause, and the whole of F-18: the drain lives in a `finally`
    now, so a trigger parked mid-turn still becomes a follow-up wakeup when the
    job is killed. `max_tries=1` (C3) means nothing else will do it."""

    async def _run(self, monkeypatch: pytest.MonkeyPatch, *, parked: tuple[str, None] | None):
        agent_id, room = uuid.uuid4(), uuid.uuid4()
        popped: list[tuple[uuid.UUID, uuid.UUID]] = []
        enqueued: list[tuple] = []

        class _Lock:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            async def __aenter__(self) -> bool:
                return True

            async def __aexit__(self, *exc: object) -> bool:
                return False

        monkeypatch.setattr(te, "turn_lock", _Lock)

        async def _pop(aid: uuid.UUID, rid: uuid.UUID):
            popped.append((aid, rid))
            return parked

        monkeypatch.setattr(te, "_pop_queued_trigger", _pop)

        async def _enqueue(*args: Any) -> None:
            enqueued.append(args)

        monkeypatch.setattr("shared_kernel.queue.enqueue", _enqueue)

        engine = te.TurnEngine.__new__(te.TurnEngine)

        async def _cancel(**kw: Any) -> te.TurnResult:
            raise asyncio.CancelledError

        engine._run_locked = _cancel  # type: ignore[attr-defined]

        with pytest.raises(asyncio.CancelledError):
            await engine.run_turn(agent_id=agent_id, chatroom_id=room, trigger="mention")

        return popped, enqueued, agent_id, room

    async def test_parked_trigger_becomes_a_follow_up_wakeup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        popped, enqueued, agent_id, room = await self._run(monkeypatch, parked=("mention", None))

        assert popped == [(agent_id, room)]
        assert enqueued == [("wakeup_agent", str(agent_id), str(room), "mention", None)]

    async def test_nothing_parked_enqueues_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        popped, enqueued, agent_id, room = await self._run(monkeypatch, parked=None)

        assert popped == [(agent_id, room)]
        assert enqueued == []
