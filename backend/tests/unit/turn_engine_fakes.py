"""Shared wiring for unit tests that drive a full `TurnEngine._run_locked` pass.

`_run_locked` reaches a dozen collaborators before it gets anywhere near the
provider stream, so every test of the turn's outcome has to stand all of them
up. Kept in one place so a new collaborator is taught to the harness once
rather than to each test module's private copy.

The four cleanup steps and the post-commit dispatches are **spied, not stubbed
away**: what these tests assert is which of them ran, so they have to stay
observable. `_stream_with_tools` is deliberately left unset — each test
supplies its own, which is how it chooses the branch under test (a reply, an
empty reply, a raise, a cancellation).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

import contexts.agents.application.runtime.turn_engine as te
from contexts.conversation.domain.models import ChatroomAgentRole
from contexts.skills.application.binding_service import BoundSet

NOW = datetime(2026, 7, 31, tzinfo=UTC)


class FakeSavepoint:
    async def __aenter__(self) -> FakeSavepoint:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class FakeDB:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def begin_nested(self) -> FakeSavepoint:
        return FakeSavepoint()


class PublisherSpy:
    """Records every room emit, and can be told to fail one event type.

    The class-level `emitted`/`fail_on` are reset by `wire_engine`; the engine
    constructs a fresh `Publisher(room)` per call site, so per-instance state
    would not survive between them.
    """

    emitted: ClassVar[list[tuple[str, str, dict]]] = []
    fail_on: ClassVar[str | None] = None
    error: ClassVar[Exception | None] = None

    def __init__(self, channel: str) -> None:
        self._channel = channel

    async def emit(self, event: str, payload: dict) -> None:
        if PublisherSpy.fail_on == event:
            raise PublisherSpy.error or RuntimeError("publish failed")
        PublisherSpy.emitted.append((self._channel, event, payload))

    @classmethod
    def events(cls, name: str) -> list[dict]:
        return [p for _, e, p in cls.emitted if e == name]


def make_agent() -> SimpleNamespace:
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


class Trace:
    """What the turn actually did, for the assertions in each test module."""

    def __init__(self) -> None:
        self.audits: list[tuple[str, dict]] = []
        self.requeued: list[tuple[list[dict], set[uuid.UUID]]] = []
        self.compact_restored: list[uuid.UUID] = []
        self.settled = 0
        self.message_signals: list[str] = []
        self.reply_wakeups: list[uuid.UUID] = []
        self.artifacts_persisted = 0

    def audited(self, action: str) -> list[dict]:
        return [extra for a, extra in self.audits if a == action]


def wire_engine(
    monkeypatch: pytest.MonkeyPatch,
    agent: SimpleNamespace,
    *,
    note: dict[str, Any],
    role: ChatroomAgentRole = ChatroomAgentRole.NORMAL,
) -> tuple[Any, Trace]:
    """Wire a full `_run_locked` pass down to the provider stream.

    Returns the engine and the `Trace` its cleanup and dispatch steps write to.
    Set `engine._stream_with_tools` before calling `_run_locked`.
    """
    PublisherSpy.emitted = []
    PublisherSpy.fail_on = None
    PublisherSpy.error = None
    trace = Trace()
    monkeypatch.setattr(te, "Publisher", PublisherSpy)

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
            return role

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
            return SimpleNamespace(id=uuid.uuid4(), created_at=NOW)

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
    engine._db = FakeDB()  # type: ignore[attr-defined]
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

    async def _signal(room: uuid.UUID, content: str) -> None:
        trace.message_signals.append(content)

    async def _wakeups(agent_: object, room: uuid.UUID, message_id: uuid.UUID) -> None:
        trace.reply_wakeups.append(message_id)

    async def _artifacts(*a: Any, **k: Any) -> int:
        trace.artifacts_persisted += 1
        return 0

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
    engine._dispatch_agent_message_signal = _signal  # type: ignore[attr-defined]
    engine._dispatch_agent_reply_wakeups = _wakeups  # type: ignore[attr-defined]
    engine._persist_artifacts = _artifacts  # type: ignore[attr-defined]
    engine._requeue_notifications = _requeue  # type: ignore[attr-defined]
    engine._restore_compact_flag = _restore  # type: ignore[attr-defined]
    engine._settle_pending_approvals = _settle  # type: ignore[attr-defined]

    return engine, trace


async def run_locked(engine: Any, room: uuid.UUID, agent: SimpleNamespace) -> te.TurnResult:
    """Invoke `_run_locked` with the arguments every test here shares."""
    return await engine._run_locked(
        agent_id=agent.id,
        chatroom_id=room,
        trigger="every_n_messages",
        parent_agent_id=None,
        input_text=None,
        request_id=None,
        trigger_message_id=None,
    )
